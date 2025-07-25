import os
import asyncio
from typing import List, Optional, Dict, Any
from fastapi import FastAPI, HTTPException, Body, Query
from pydantic import BaseModel
from starlette.responses import JSONResponse
from pymodbus.client.async_tcp import AsyncModbusTCPClient
from pymodbus.exceptions import ModbusException

# ENVIRONMENT VARIABLES
MODBUS_HOST = os.environ.get("DEVICE_IP")
MODBUS_PORT = int(os.environ.get("MODBUS_TCP_PORT", "502"))
MODBUS_UNIT_ID = int(os.environ.get("MODBUS_UNIT_ID", "1"))
HTTP_HOST = os.environ.get("HTTP_HOST", "0.0.0.0")
HTTP_PORT = int(os.environ.get("HTTP_PORT", "8080"))

# REGISTER MAPS
DEVICE_REGS = {
    "model": (30000, 10),  # 10 registers (string), modbus address is 0-based
    "serial_number": (30015, 10),  # 10 registers (string)
    "firmware_version": (30035, 6),  # 6 registers (string)
    "rated_power": (30073, 2),  # 2 registers (uint32)
}

TELEMETRY_REGS = {
    "active_power": (32080, 2),  # int32 (W)
    "reactive_power": (32082, 2),  # int32 (var)
    "voltages": [
        ("phase_a", 32066, 2),  # float32 (V)
        ("phase_b", 32068, 2),
        ("phase_c", 32070, 2),
    ],
    "current": [
        ("phase_a", 32072, 2),  # float32 (A)
        ("phase_b", 32074, 2),
        ("phase_c", 32076, 2),
    ],
    "power_factor": (32084, 2),  # float32
    "frequency": (32085, 2),  # float32 (Hz)
    "total_energy": (32106, 2),  # uint32 (kWh)
    "alarm_codes": (32090, 2),  # uint32
}

# UTILS
def decode_string(registers: List[int]) -> str:
    # Each register is 2 bytes, decode as utf-8 ignoring nulls
    b = bytearray()
    for reg in registers:
        b += reg.to_bytes(2, 'big')
    return b.decode('ascii', errors='ignore').strip('\x00').strip()

def decode_uint32(registers: List[int]) -> int:
    return (registers[0] << 16) + registers[1]

def decode_int32(registers: List[int]) -> int:
    val = (registers[0] << 16) + registers[1]
    if val & 0x80000000:
        val -= 0x100000000
    return val

def decode_float32(registers: List[int]) -> float:
    import struct
    b = (registers[0] << 16) + registers[1]
    return struct.unpack('>f', b.to_bytes(4, 'big'))[0]

# FastAPI
app = FastAPI(title="Huawei SUN2000 Solar Inverter Shifu HTTP Driver")

# Connection Pool
class ModbusTCPPool:
    def __init__(self):
        self.client = None

    async def connect(self):
        if self.client is None:
            self.client = AsyncModbusTCPClient(host=MODBUS_HOST, port=MODBUS_PORT)
            await self.client.connect()
        elif not self.client.connected:
            await self.client.connect()
        return self.client

    async def close(self):
        if self.client is not None:
            await self.client.close()
            self.client = None

modbus_pool = ModbusTCPPool()

# API MODELS
class ControlCommand(BaseModel):
    # Example: { "registers": [{"address": 42006, "value": 5000}], "unit_id": 1 }
    registers: List[Dict[str, Any]]
    unit_id: Optional[int] = None

@app.on_event("shutdown")
async def shutdown_event():
    await modbus_pool.close()

async def read_holding_registers(address: int, count: int, unit: int) -> List[int]:
    client = await modbus_pool.connect()
    try:
        rr = await client.protocol.read_holding_registers(address, count, unit=unit)
    except ModbusException as e:
        raise HTTPException(status_code=502, detail=f"Modbus error: {str(e)}")
    if rr.isError():
        raise HTTPException(status_code=502, detail=f"Modbus error: {str(rr)}")
    return list(rr.registers)

async def write_single_register(address: int, value: int, unit: int):
    client = await modbus_pool.connect()
    try:
        rq = await client.protocol.write_register(address, value, unit=unit)
    except ModbusException as e:
        raise HTTPException(status_code=502, detail=f"Modbus error: {str(e)}")
    if rq.isError():
        raise HTTPException(status_code=502, detail=f"Modbus write error: {str(rq)}")
    return True

async def write_multiple_registers(address: int, values: List[int], unit: int):
    client = await modbus_pool.connect()
    try:
        rq = await client.protocol.write_registers(address, values, unit=unit)
    except ModbusException as e:
        raise HTTPException(status_code=502, detail=f"Modbus error: {str(e)}")
    if rq.isError():
        raise HTTPException(status_code=502, detail=f"Modbus write error: {str(rq)}")
    return True

# ENDPOINTS

@app.get("/device")
async def get_device_info():
    unit = MODBUS_UNIT_ID
    info = {}
    # Model
    regs = await read_holding_registers(DEVICE_REGS["model"][0], DEVICE_REGS["model"][1], unit)
    info["model"] = decode_string(regs)
    # Serial
    regs = await read_holding_registers(DEVICE_REGS["serial_number"][0], DEVICE_REGS["serial_number"][1], unit)
    info["serial_number"] = decode_string(regs)
    # Firmware
    regs = await read_holding_registers(DEVICE_REGS["firmware_version"][0], DEVICE_REGS["firmware_version"][1], unit)
    info["firmware_version"] = decode_string(regs)
    # Rated power
    regs = await read_holding_registers(DEVICE_REGS["rated_power"][0], DEVICE_REGS["rated_power"][1], unit)
    info["rated_power"] = decode_uint32(regs)
    return JSONResponse(info)

@app.get("/telemetry")
async def get_telemetry(
    metrics: Optional[List[str]] = Query(default=None, description="Metrics to fetch (active_power,reactive_power,voltages,current,power_factor,frequency,total_energy,alarm_codes)")
):
    unit = MODBUS_UNIT_ID
    data = {}
    include_all = metrics is None or len(metrics) == 0

    if include_all or "active_power" in metrics:
        regs = await read_holding_registers(*TELEMETRY_REGS["active_power"], unit)
        data["active_power"] = decode_int32(regs)
    if include_all or "reactive_power" in metrics:
        regs = await read_holding_registers(*TELEMETRY_REGS["reactive_power"], unit)
        data["reactive_power"] = decode_int32(regs)
    if include_all or "voltages" in metrics:
        voltages = {}
        for name, addr, cnt in TELEMETRY_REGS["voltages"]:
            regs = await read_holding_registers(addr, cnt, unit)
            voltages[name] = decode_float32(regs)
        data["voltages"] = voltages
    if include_all or "current" in metrics:
        currents = {}
        for name, addr, cnt in TELEMETRY_REGS["current"]:
            regs = await read_holding_registers(addr, cnt, unit)
            currents[name] = decode_float32(regs)
        data["current"] = currents
    if include_all or "power_factor" in metrics:
        regs = await read_holding_registers(*TELEMETRY_REGS["power_factor"], unit)
        data["power_factor"] = decode_float32(regs)
    if include_all or "frequency" in metrics:
        regs = await read_holding_registers(*TELEMETRY_REGS["frequency"], unit)
        data["frequency"] = decode_float32(regs)
    if include_all or "total_energy" in metrics:
        regs = await read_holding_registers(*TELEMETRY_REGS["total_energy"], unit)
        data["total_energy"] = decode_uint32(regs)
    if include_all or "alarm_codes" in metrics:
        regs = await read_holding_registers(*TELEMETRY_REGS["alarm_codes"], unit)
        data["alarm_codes"] = decode_uint32(regs)
    return JSONResponse(data)

@app.put("/control")
async def control_device(command: ControlCommand = Body(...)):
    unit = command.unit_id if command.unit_id is not None else MODBUS_UNIT_ID
    results = []
    for reg in command.registers:
        addr = reg["address"]
        value = reg["value"]
        if isinstance(value, list):
            await write_multiple_registers(addr, value, unit)
        else:
            await write_single_register(addr, value, unit)
        results.append({"address": addr, "status": "ok"})
    return {"results": results}

# RUN SERVER
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=HTTP_HOST, port=HTTP_PORT, reload=False)