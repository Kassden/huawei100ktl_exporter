import os
import asyncio
from typing import List, Optional, Dict, Any
from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from dotenv import load_dotenv

from pymodbus.client.async_tcp import AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException

load_dotenv()

DEVICE_IP = os.environ.get("DEVICE_IP", "127.0.0.1")
MODBUS_TCP_PORT = int(os.environ.get("MODBUS_TCP_PORT", 502))
HTTP_HOST = os.environ.get("HTTP_HOST", "0.0.0.0")
HTTP_PORT = int(os.environ.get("HTTP_PORT", 8080))
MODBUS_UNIT_ID = int(os.environ.get("MODBUS_UNIT_ID", 1))
MODBUS_TIMEOUT = float(os.environ.get("MODBUS_TIMEOUT", 3.0))

app = FastAPI(title="Huawei SUN2000 Solar Inverter HTTP DeviceShifu Driver")

REGISTER_MAP = {
    "model": (30000, 6, "str"),
    "serial_number": (30015, 10, "str"),
    "firmware_version": (30035, 6, "str"),
    "rated_power": (30073, 2, "uint32"),
    "active_power": (32080, 2, "int32"),
    "reactive_power": (32082, 2, "int32"),
    "voltages": (32066, 6, "list_uint16"),  # Uab, Ubc, Uca, Ua, Ub, Uc
    "power_factor": (32084, 2, "int32"),
    "frequency": (32085, 2, "uint32"),
    "total_energy": (32106, 2, "uint32"),
    "alarm_codes": (32090, 2, "uint32"),
}

TELEMETRY_DEFAULT_KEYS = [
    "active_power",
    "reactive_power",
    "voltages",
    "power_factor",
    "frequency",
    "total_energy",
    "alarm_codes"
]

DEVICE_INFO_KEYS = [
    "model",
    "serial_number",
    "firmware_version",
    "rated_power"
]

CONTROL_WRITE_MAP = {
    # Example control registers for illustration; actual mapping may differ
    "active_power_limit": (42000, "uint16"),
    "reactive_power_limit": (42002, "uint16"),
    # add other control registers as needed
}

class ControlPayload(BaseModel):
    registers: Dict[str, Any]

def _decode_registers(raw, data_type):
    if data_type == "str":
        return "".join(chr((r >> 8) & 0xFF) + chr(r & 0xFF) for r in raw).rstrip('\x00')
    elif data_type == "uint32":
        if len(raw) >= 2:
            return (raw[0] << 16) + raw[1]
        return None
    elif data_type == "int32":
        if len(raw) >= 2:
            val = (raw[0] << 16) + raw[1]
            if val & (1 << 31):
                val -= 1 << 32
            return val
        return None
    elif data_type == "list_uint16":
        return list(raw)
    elif data_type == "uint16":
        return raw[0] if len(raw) else None
    else:
        return raw

async def get_modbus_client():
    client = AsyncModbusTcpClient(
        host=DEVICE_IP,
        port=MODBUS_TCP_PORT,
        timeout=MODBUS_TIMEOUT
    )
    await client.connect()
    if not client.connected:
        raise HTTPException(status_code=503, detail="Cannot connect to inverter via Modbus TCP")
    return client

async def read_registers(register: int, count: int, unit: int = MODBUS_UNIT_ID) -> List[int]:
    client = await get_modbus_client()
    try:
        rr = await client.read_holding_registers(register, count, unit=unit)
        if not hasattr(rr, "registers") or rr.isError():
            raise HTTPException(status_code=500, detail=f"Modbus error reading {register}")
        return rr.registers
    finally:
        await client.close()

async def write_registers(register: int, values: List[int], unit: int = MODBUS_UNIT_ID):
    client = await get_modbus_client()
    try:
        if len(values) == 1:
            wr = await client.write_register(register, values[0], unit=unit)
        else:
            wr = await client.write_registers(register, values, unit=unit)
        if wr.isError():
            raise HTTPException(status_code=500, detail=f"Modbus error writing {register}")
    finally:
        await client.close()

@app.get("/device")
async def get_device_info():
    result = {}
    for key in DEVICE_INFO_KEYS:
        reg, count, dtype = REGISTER_MAP[key]
        raw = await read_registers(reg, count)
        result[key] = _decode_registers(raw, dtype)
    return JSONResponse(result)

@app.get("/telemetry")
async def get_telemetry(
    metrics: Optional[List[str]] = Query(None, description="List of telemetry fields to return")
):
    fields = metrics if metrics else TELEMETRY_DEFAULT_KEYS
    result = {}
    for key in fields:
        if key not in REGISTER_MAP:
            continue
        reg, count, dtype = REGISTER_MAP[key]
        raw = await read_registers(reg, count)
        result[key] = _decode_registers(raw, dtype)
    return JSONResponse(result)

@app.put("/control")
async def put_control(payload: ControlPayload):
    for k, v in payload.registers.items():
        if k not in CONTROL_WRITE_MAP:
            raise HTTPException(status_code=400, detail=f"Unknown control parameter: {k}")
        reg, dtype = CONTROL_WRITE_MAP[k]
        if dtype == "uint16":
            values = [int(v)]
        elif dtype == "int16":
            values = [int(v) & 0xFFFF]
        elif dtype == "uint32":
            values = [(int(v) >> 16) & 0xFFFF, int(v) & 0xFFFF]
        elif dtype == "int32":
            val = int(v)
            if val < 0:
                val += 1 << 32
            values = [(val >> 16) & 0xFFFF, val & 0xFFFF]
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported type for {k}")
        await write_registers(reg, values)
    return JSONResponse({"status": "success", "written": list(payload.registers.keys())})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=HTTP_HOST, port=HTTP_PORT, reload=False)