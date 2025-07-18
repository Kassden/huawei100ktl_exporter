import os
import asyncio
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from pymodbus.client.asynchronous.tcp import AsyncModbusTCPClient
from pymodbus.client.asynchronous.serial import AsyncModbusSerialClient
from pymodbus.payload import BinaryPayloadBuilder, BinaryPayloadDecoder
from pymodbus.constants import Endian
from pymodbus.exceptions import ModbusException
import uvicorn

# Configuration from environment variables
MODBUS_MODE = os.getenv("MODBUS_MODE", "tcp").lower()  # "tcp" or "rtu"
MODBUS_TCP_HOST = os.getenv("MODBUS_TCP_HOST", "127.0.0.1")
MODBUS_TCP_PORT = int(os.getenv("MODBUS_TCP_PORT", "502"))
MODBUS_RTU_PORT = os.getenv("MODBUS_RTU_PORT", "/dev/ttyUSB0")
MODBUS_RTU_BAUDRATE = int(os.getenv("MODBUS_RTU_BAUDRATE", "9600"))
MODBUS_RTU_PARITY = os.getenv("MODBUS_RTU_PARITY", "N")
MODBUS_RTU_STOPBITS = int(os.getenv("MODBUS_RTU_STOPBITS", "1"))
MODBUS_RTU_BYTESIZE = int(os.getenv("MODBUS_RTU_BYTESIZE", "8"))
MODBUS_UNIT_ID = int(os.getenv("MODBUS_UNIT_ID", "1"))
SERVER_HOST = os.getenv("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("SERVER_PORT", "8080"))
MODBUS_TIMEOUT = float(os.getenv("MODBUS_TIMEOUT", "5.0"))
MODBUS_RETRIES = int(os.getenv("MODBUS_RETRIES", "1"))

# Register Map
REGISTER_MAP = {
    "model": (30000, 10, "string"),
    "serial_number": (30015, 10, "string"),
    "firmware_version": (30035, 5, "string"),
    "rated_power": (30073, 2, "uint32"),
    "active_power": (32080, 2, "int32"),
    "reactive_power": (32082, 2, "int32"),
    "voltages": (32066, 6, "multi_uint16"),
    "power_factor": (32084, 2, "int32"),
    "frequency": (32085, 2, "uint32"),
    "total_energy": (32106, 4, "uint64"),
    "alarm_codes": (32090, 4, "multi_uint16"),
}

TELEMETRY_REGISTERS = {
    "active_power": REGISTER_MAP["active_power"],
    "reactive_power": REGISTER_MAP["reactive_power"],
    "voltages": REGISTER_MAP["voltages"],
    "power_factor": REGISTER_MAP["power_factor"],
    "frequency": REGISTER_MAP["frequency"],
    "total_energy": REGISTER_MAP["total_energy"],
    "alarm_codes": REGISTER_MAP["alarm_codes"],
}

DEVICE_REGISTERS = {
    "model": REGISTER_MAP["model"],
    "serial_number": REGISTER_MAP["serial_number"],
    "firmware_version": REGISTER_MAP["firmware_version"],
    "rated_power": REGISTER_MAP["rated_power"],
}

# FastAPI setup
app = FastAPI(title="Huawei SUN2000 DeviceShifu Driver")

# Modbus Client references
modbus_client = None
modbus_protocol = None

# Utility: Decode Modbus register data
def decode_value(register_type: str, registers: List[int]) -> Any:
    decoder = BinaryPayloadDecoder.fromRegisters(registers, byteorder=Endian.Big, wordorder=Endian.Big)
    if register_type == "string":
        s = "".join(chr((reg >> 8) & 0xFF) + chr(reg & 0xFF) for reg in registers)
        return s.strip("\x00 ")
    elif register_type == "uint32":
        return decoder.decode_32bit_uint()
    elif register_type == "int32":
        return decoder.decode_32bit_int()
    elif register_type == "uint64":
        return decoder.decode_64bit_uint()
    elif register_type == "multi_uint16":
        return list(registers)
    else:
        return registers

# Utility: Read holding registers with retries
async def modbus_read(address: int, count: int) -> List[int]:
    for attempt in range(MODBUS_RETRIES + 1):
        try:
            if MODBUS_MODE == "tcp":
                rr = await modbus_client.read_holding_registers(address, count, unit=MODBUS_UNIT_ID)
            else:
                rr = await modbus_client.read_holding_registers(address, count, unit=MODBUS_UNIT_ID)
            if rr.isError():
                raise ModbusException(str(rr))
            return rr.registers
        except Exception as e:
            if attempt == MODBUS_RETRIES:
                raise
            await asyncio.sleep(0.2)
    raise HTTPException(status_code=500, detail="Modbus read failed.")

# Utility: Write holding registers with retries (single or multiple)
async def modbus_write(address: int, values: List[int]) -> None:
    for attempt in range(MODBUS_RETRIES + 1):
        try:
            if len(values) == 1:
                rr = await modbus_client.write_register(address, values[0], unit=MODBUS_UNIT_ID)
            else:
                rr = await modbus_client.write_registers(address, values, unit=MODBUS_UNIT_ID)
            if rr.isError():
                raise ModbusException(str(rr))
            return
        except Exception as e:
            if attempt == MODBUS_RETRIES:
                raise
            await asyncio.sleep(0.2)
    raise HTTPException(status_code=500, detail="Modbus write failed.")

# ----------- API Models -----------

class ControlRequest(BaseModel):
    # Example: {"registers": [{"address":42000, "values":[100]}, ...]}
    registers: List[Dict[str, Any]]

class ControlResponse(BaseModel):
    success: bool
    details: str

# ----------- API Endpoints -----------

@app.get("/device", response_class=JSONResponse)
async def get_device_info():
    result = {}
    try:
        for field, (address, count, rtype) in DEVICE_REGISTERS.items():
            regs = await modbus_read(address, count)
            result[field] = decode_value(rtype, regs)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read device info: {str(e)}")
    return result

@app.get("/telemetry", response_class=JSONResponse)
async def get_telemetry(
    metrics: Optional[List[str]] = Query(None, description="Comma-separated list of metrics to include (e.g., active_power,voltages)")
):
    result = {}
    targets = TELEMETRY_REGISTERS
    if metrics:
        targets = {k: v for k, v in TELEMETRY_REGISTERS.items() if k in metrics}
        if not targets:
            raise HTTPException(status_code=400, detail="No valid metrics specified.")
    try:
        for field, (address, count, rtype) in targets.items():
            regs = await modbus_read(address, count)
            result[field] = decode_value(rtype, regs)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read telemetry: {str(e)}")
    return result

@app.put("/control", response_model=ControlResponse)
async def put_control(cmd: ControlRequest):
    try:
        for reg in cmd.registers:
            address = reg["address"]
            values = reg["values"]
            if not isinstance(values, list):
                values = [values]
            await modbus_write(address, values)
        return ControlResponse(success=True, details="All commands executed successfully.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Control operation failed: {str(e)}")

# ----------- Modbus Client Management -----------

async def setup_modbus_client():
    global modbus_client, modbus_protocol
    if MODBUS_MODE == "tcp":
        _, modbus_client = await AsyncModbusTCPClient(
            host=MODBUS_TCP_HOST, 
            port=MODBUS_TCP_PORT,
            timeout=MODBUS_TIMEOUT
        )
    elif MODBUS_MODE == "rtu":
        _, modbus_client = await AsyncModbusSerialClient(
            method="rtu",
            port=MODBUS_RTU_PORT,
            baudrate=MODBUS_RTU_BAUDRATE,
            parity=MODBUS_RTU_PARITY,
            stopbits=MODBUS_RTU_STOPBITS,
            bytesize=MODBUS_RTU_BYTESIZE,
            timeout=MODBUS_TIMEOUT
        )
    else:
        raise ValueError("Unsupported MODBUS_MODE. Use 'tcp' or 'rtu'.")

@app.on_event("startup")
async def on_startup():
    await setup_modbus_client()

@app.on_event("shutdown")
async def on_shutdown():
    if modbus_client:
        await modbus_client.close()

# ----------- Uvicorn Entrypoint -----------

def main():
    uvicorn.run("main:app", host=SERVER_HOST, port=SERVER_PORT, reload=False, access_log=False)

if __name__ == "__main__":
    main()