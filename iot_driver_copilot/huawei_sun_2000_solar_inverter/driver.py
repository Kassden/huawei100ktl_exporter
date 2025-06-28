import os
import asyncio
from typing import Optional, List
from fastapi import FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from pymodbus.client.async_tcp import AsyncModbusTcpClient
from pymodbus.constants import Defaults
from pymodbus.exceptions import ModbusException
import struct

# Config from environment variables
MODBUS_HOST = os.environ.get("SUN2000_MODBUS_HOST", "127.0.0.1")
MODBUS_PORT = int(os.environ.get("SUN2000_MODBUS_PORT", "502"))
MODBUS_UNIT_ID = int(os.environ.get("SUN2000_MODBUS_UNIT_ID", "1"))
HTTP_HOST = os.environ.get("SHIFU_HTTP_HOST", "0.0.0.0")
HTTP_PORT = int(os.environ.get("SHIFU_HTTP_PORT", "8080"))
MODBUS_TIMEOUT = float(os.environ.get("SUN2000_MODBUS_TIMEOUT", "5.0"))

# Register addresses (Huawei SUN2000)
REGISTERS = {
    "model": 30000,
    "serial_number": 30015,
    "firmware_version": 30035,
    "rated_power": 30073,
    "active_power": 32080,
    "reactive_power": 32082,
    "voltage_L1": 32066,
    "voltage_L2": 32067,
    "voltage_L3": 32068,
    "current_L1": 32069,
    "current_L2": 32070,
    "current_L3": 32071,
    "power_factor": 32084,
    "frequency": 32085,
    "total_energy": 32106,
    "alarm_codes": 32090,
}

# Mapping of telemetry keys to register and type info
TELEMETRY_MAP = {
    "active_power": {"address": 32080, "count": 2, "type": "int32", "scale": 0.01, "unit": "W"},
    "reactive_power": {"address": 32082, "count": 2, "type": "int32", "scale": 0.01, "unit": "var"},
    "voltage_L1": {"address": 32066, "count": 2, "type": "int32", "scale": 0.01, "unit": "V"},
    "voltage_L2": {"address": 32067, "count": 2, "type": "int32", "scale": 0.01, "unit": "V"},
    "voltage_L3": {"address": 32068, "count": 2, "type": "int32", "scale": 0.01, "unit": "V"},
    "current_L1": {"address": 32069, "count": 2, "type": "int32", "scale": 0.01, "unit": "A"},
    "current_L2": {"address": 32070, "count": 2, "type": "int32", "scale": 0.01, "unit": "A"},
    "current_L3": {"address": 32071, "count": 2, "type": "int32", "scale": 0.01, "unit": "A"},
    "power_factor": {"address": 32084, "count": 2, "type": "int32", "scale": 0.001, "unit": ""},
    "frequency": {"address": 32085, "count": 2, "type": "int32", "scale": 0.01, "unit": "Hz"},
    "total_energy": {"address": 32106, "count": 2, "type": "int32", "scale": 1, "unit": "Wh"},
    "alarm_codes": {"address": 32090, "count": 2, "type": "int32", "scale": 1, "unit": ""},
}

DEVICE_MAP = {
    "model": {"address": 30000, "count": 10, "type": "string", "unit": ""},
    "serial_number": {"address": 30015, "count": 10, "type": "string", "unit": ""},
    "firmware_version": {"address": 30035, "count": 6, "type": "string", "unit": ""},
    "rated_power": {"address": 30073, "count": 2, "type": "int32", "scale": 1, "unit": "W"},
}

CONTROL_MAP = {
    # Example writable registers for control. These should be updated with the actual ones as per device manual.
    "active_power_limit": {"address": 42000, "count": 2, "type": "int32", "scale": 1},
    "reactive_power_limit": {"address": 42002, "count": 2, "type": "int32", "scale": 1},
    # Add more as per device documentation
}

# FastAPI app
app = FastAPI(title="Huawei SUN2000 DeviceShifu Driver")

class ControlCommand(BaseModel):
    commands: List[dict] = Field(..., description="List of control commands with keys: 'name' (eg. 'active_power_limit'), 'value'")

def parse_string_registers(registers):
    """Convert list of registers to string (2 bytes per register, big endian utf-8)."""
    raw = b"".join(struct.pack(">H", r) for r in registers)
    # Remove trailing nulls/garbage
    return raw.decode("utf-8", errors="ignore").replace('\x00', '').strip()

def parse_int32_registers(registers):
    """Convert two 16-bit registers to signed 32-bit integer (big endian)"""
    if len(registers) < 2:
        return None
    return struct.unpack(">i", struct.pack(">HH", registers[0], registers[1]))[0]

def build_int32_registers(value):
    """Convert signed 32-bit integer to two 16-bit registers (big endian)"""
    packed = struct.pack(">i", int(value))
    return [struct.unpack(">H", packed[:2])[0], struct.unpack(">H", packed[2:])[0]]

class ModbusTCPClient:
    def __init__(self, host, port, unit_id, timeout=5.0):
        self.host = host
        self.port = port
        self.unit_id = unit_id
        self.timeout = timeout
        self.client = None

    async def connect(self):
        self.client = AsyncModbusTcpClient(
            self.host,
            port=self.port,
            timeout=self.timeout,
        )
        await self.client.connect()

    async def close(self):
        if self.client:
            await self.client.close()

    async def read_holding_registers(self, address, count):
        if self.client is None or not self.client.connected:
            await self.connect()
        resp = await self.client.read_holding_registers(address, count, unit=self.unit_id)
        if resp.isError():
            raise HTTPException(status_code=502, detail=f"Modbus read error: {resp}")
        return resp.registers

    async def write_registers(self, address, values):
        if self.client is None or not self.client.connected:
            await self.connect()
        if len(values) == 1:
            resp = await self.client.write_register(address, values[0], unit=self.unit_id)
        else:
            resp = await self.client.write_registers(address, values, unit=self.unit_id)
        if resp.isError():
            raise HTTPException(status_code=502, detail=f"Modbus write error: {resp}")

# FastAPI event handlers for startup and shutdown to keep Modbus client alive
modbus_client = ModbusTCPClient(MODBUS_HOST, MODBUS_PORT, MODBUS_UNIT_ID, timeout=MODBUS_TIMEOUT)

@app.on_event("startup")
async def startup_event():
    await modbus_client.connect()

@app.on_event("shutdown")
async def shutdown_event():
    await modbus_client.close()

@app.get("/device", summary="Get device information")
async def get_device():
    try:
        result = {}
        for key, spec in DEVICE_MAP.items():
            regs = await modbus_client.read_holding_registers(spec["address"], spec["count"])
            if spec["type"] == "string":
                value = parse_string_registers(regs)
            elif spec["type"] == "int32":
                value = parse_int32_registers(regs)
            else:
                value = regs
            result[key] = value
        return JSONResponse(result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/telemetry", summary="Get real-time telemetry")
async def get_telemetry(metrics: Optional[List[str]] = Query(None)):
    try:
        result = {}
        if metrics:
            # User requested specific telemetry fields
            for m in metrics:
                if m not in TELEMETRY_MAP:
                    continue
                spec = TELEMETRY_MAP[m]
                regs = await modbus_client.read_holding_registers(spec["address"], spec["count"])
                value = parse_int32_registers(regs)
                if value is not None:
                    value = round(value * spec.get("scale", 1), 3)
                result[m] = value
        else:
            # Return all telemetry fields
            for key, spec in TELEMETRY_MAP.items():
                regs = await modbus_client.read_holding_registers(spec["address"], spec["count"])
                value = parse_int32_registers(regs)
                if value is not None:
                    value = round(value * spec.get("scale", 1), 3)
                result[key] = value
        return JSONResponse(result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/control", summary="Remote control of inverter")
async def control_device(cmd: ControlCommand):
    try:
        results = []
        for command in cmd.commands:
            name = command.get("name")
            value = command.get("value")
            if name not in CONTROL_MAP:
                results.append({"name": name, "status": "error", "message": "Unknown command"})
                continue
            spec = CONTROL_MAP[name]
            # Prepare value for register
            regs = build_int32_registers(value)
            await modbus_client.write_registers(spec["address"], regs)
            results.append({"name": name, "status": "ok"})
        return JSONResponse({"results": results})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HTTP_HOST, port=HTTP_PORT)