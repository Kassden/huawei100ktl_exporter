import os
import json
import asyncio
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.middleware.cors import CORSMiddleware
from pymodbus.client.async_tcp import AsyncModbusTCPClient
from pymodbus.client import AsyncModbusSerialClient
from pymodbus.constants import Endian
from pymodbus.payload import BinaryPayloadDecoder, BinaryPayloadBuilder

# --- Configuration from Environment Variables ---
DEVICE_IP = os.environ.get("DEVICE_IP", "127.0.0.1")
MODBUS_TCP_PORT = int(os.environ.get("MODBUS_TCP_PORT", "502"))
MODBUS_RTU_PORT = os.environ.get("MODBUS_RTU_PORT")  # e.g., "/dev/ttyUSB0"
MODBUS_RTU_BAUDRATE = int(os.environ.get("MODBUS_RTU_BAUDRATE", "9600"))
MODBUS_SLAVE_ID = int(os.environ.get("MODBUS_SLAVE_ID", "1"))

SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8080"))
PROTOCOL = os.environ.get("PROTOCOL", "TCP").upper()  # "TCP" or "RTU"

# --- Register Map (Huawei SUN2000) ---
REGISTERS = {
    "model": (30000, 6, "string"),                  # 6 registers (12 bytes)
    "serial_number": (30015, 10, "string"),         # 10 registers (20 bytes)
    "firmware_version": (30035, 6, "string"),       # 6 registers (12 bytes)
    "rated_power": (30073, 2, "uint32"),            # 2 registers (uint32)
    "active_power": (32080, 2, "int32"),            # 2 registers (int32)
    "reactive_power": (32082, 2, "int32"),          # 2 registers (int32)
    "voltage_ph1": (32066, 2, "uint32"),            # 2 registers (uint32)
    "voltage_ph2": (32068, 2, "uint32"),
    "voltage_ph3": (32070, 2, "uint32"),
    "current_ph1": (32072, 2, "int32"),
    "current_ph2": (32074, 2, "int32"),
    "current_ph3": (32076, 2, "int32"),
    "power_factor": (32084, 2, "int32"),
    "frequency": (32085, 2, "uint32"),
    "total_energy": (32106, 2, "uint32"),           # 2 registers (uint32), Wh (divide by 100 for kWh)
    "alarm_code": (32090, 1, "uint16"),
}

# --- FastAPI App ---
app = FastAPI(title="Huawei SUN2000 Solar Inverter HTTP DeviceShifu Driver")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Modbus Connection Management ---
class ModbusClient:
    _tcp_client: Optional[AsyncModbusTCPClient] = None
    _rtu_client: Optional[AsyncModbusSerialClient] = None

    @classmethod
    async def get_client(cls):
        if PROTOCOL == "TCP":
            if cls._tcp_client is None:
                cls._tcp_client = AsyncModbusTCPClient(
                    host=DEVICE_IP, port=MODBUS_TCP_PORT, timeout=3
                )
                await cls._tcp_client.connect()
            return cls._tcp_client
        elif PROTOCOL == "RTU":
            if not MODBUS_RTU_PORT:
                raise RuntimeError("MODBUS_RTU_PORT environment variable not set.")
            if cls._rtu_client is None:
                cls._rtu_client = AsyncModbusSerialClient(
                    method="rtu",
                    port=MODBUS_RTU_PORT,
                    baudrate=MODBUS_RTU_BAUDRATE,
                    stopbits=1,
                    bytesize=8,
                    parity="N",
                    timeout=3,
                )
                await cls._rtu_client.connect()
            return cls._rtu_client
        else:
            raise RuntimeError(f"Unsupported protocol: {PROTOCOL}")

    @classmethod
    async def close_clients(cls):
        if cls._tcp_client is not None:
            await cls._tcp_client.close()
        if cls._rtu_client is not None:
            await cls._rtu_client.close()

# --- Utility Functions ---
def decode_string(registers: List[int]) -> str:
    byte_array = bytearray()
    for r in registers:
        byte_array += r.to_bytes(2, "big")
    return byte_array.decode("ascii", errors="ignore").strip("\x00 ")

def decode_uint32(registers: List[int]) -> int:
    return (registers[0] << 16) + registers[1]

def decode_int32(registers: List[int]) -> int:
    value = (registers[0] << 16) + registers[1]
    if value & 0x80000000:
        value -= 0x100000000
    return value

def decode_uint16(registers: List[int]) -> int:
    return registers[0]

def decode_value(data_type: str, registers: List[int]) -> Any:
    if data_type == "string":
        return decode_string(registers)
    elif data_type == "uint32":
        return decode_uint32(registers)
    elif data_type == "int32":
        return decode_int32(registers)
    elif data_type == "uint16":
        return decode_uint16(registers)
    else:
        return registers

async def read_register(address: int, count: int) -> List[int]:
    client = await ModbusClient.get_client()
    resp = await client.protocol.read_holding_registers(address, count, unit=MODBUS_SLAVE_ID)
    if not resp or hasattr(resp, "isError") and resp.isError():
        raise HTTPException(status_code=502, detail="Modbus read error")
    return list(resp.registers)

async def write_register(address: int, values: List[int]) -> None:
    client = await ModbusClient.get_client()
    if len(values) == 1:
        resp = await client.protocol.write_register(address, values[0], unit=MODBUS_SLAVE_ID)
    else:
        resp = await client.protocol.write_registers(address, values, unit=MODBUS_SLAVE_ID)
    if not resp or hasattr(resp, "isError") and resp.isError():
        raise HTTPException(status_code=502, detail="Modbus write error")

# --- API Models ---
class ControlPayload(BaseModel):
    registers: Dict[str, Any]

# --- API Endpoints ---
@app.get("/device")
async def get_device_info():
    try:
        model_regs = await read_register(*REGISTERS["model"][:2])
        serial_regs = await read_register(*REGISTERS["serial_number"][:2])
        fw_regs = await read_register(*REGISTERS["firmware_version"][:2])
        rated_power_regs = await read_register(*REGISTERS["rated_power"][:2])

        return {
            "model": decode_value("string", model_regs),
            "serial_number": decode_value("string", serial_regs),
            "firmware_version": decode_value("string", fw_regs),
            "rated_power": decode_value("uint32", rated_power_regs)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/telemetry")
async def get_telemetry(
    metrics: Optional[List[str]] = Query(None, description="Metrics to return (e.g. active_power, voltage_ph1)")):
    available_metrics = {
        "active_power", "reactive_power", "voltage_ph1", "voltage_ph2", "voltage_ph3",
        "current_ph1", "current_ph2", "current_ph3", "power_factor",
        "frequency", "total_energy", "alarm_code"
    }
    if metrics:
        metrics = set(metrics)
        invalid = metrics - available_metrics
        if invalid:
            raise HTTPException(status_code=400, detail=f"Invalid metrics: {invalid}")
    else:
        metrics = available_metrics

    result = {}
    try:
        for m in metrics:
            address, count, dtype = REGISTERS[m]
            regs = await read_register(address, count)
            val = decode_value(dtype, regs)
            # Some scaling
            if m == "total_energy":
                val = val / 100  # Wh to kWh
            elif m == "frequency":
                val = val / 100  # Hz
            elif m.startswith("voltage") or m.startswith("current") or m in ("active_power", "reactive_power", "power_factor"):
                val = val / 100
            result[m] = val
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/control")
async def put_control(payload: ControlPayload):
    """
    Payload example:
    {
        "registers": {
            "42006": 1000,              # Write value 1000 to register 42006
            "42010": [0, 1, 2, 3]       # Write values to registers 42010-42013
        }
    }
    """
    try:
        for reg_addr_str, value in payload.registers.items():
            reg_addr = int(reg_addr_str)
            if isinstance(value, list):
                await write_register(reg_addr, value)
            else:
                await write_register(reg_addr, [value])
        return {"status": "OK"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- Graceful Shutdown ---
@app.on_event("shutdown")
async def shutdown_event():
    await ModbusClient.close_clients()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=SERVER_HOST, port=SERVER_PORT)