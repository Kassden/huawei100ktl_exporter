import os
import asyncio
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, HTTPException, Query, Body
from pydantic import BaseModel
from starlette.responses import JSONResponse
from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException

# Environment variables
MODBUS_HOST = os.getenv("MODBUS_HOST", "127.0.0.1")
MODBUS_PORT = int(os.getenv("MODBUS_PORT", "502"))
MODBUS_UNIT_ID = int(os.getenv("MODBUS_UNIT_ID", "1"))

HTTP_HOST = os.getenv("HTTP_HOST", "0.0.0.0")
HTTP_PORT = int(os.getenv("HTTP_PORT", "8080"))

MODBUS_TIMEOUT = float(os.getenv("MODBUS_TIMEOUT", "3.0"))

app = FastAPI(title="Huawei SUN2000 Solar Inverter HTTP Driver")

# Register map
REGISTER_MAP = {
    "model": (30000, 6),  # 6 registers (12 bytes, string)
    "serial_number": (30015, 10),  # 10 registers (20 bytes, string)
    "firmware_version": (30035, 6),  # 6 registers (12 bytes, string)
    "rated_power": (30073, 2),  # 2 registers (4 bytes, uint32)
    "active_power": (32080, 2),  # 2 registers (4 bytes, int32)
    "reactive_power": (32082, 2),  # 2 registers (4 bytes, int32)
    "voltages": (32066, 6),  # 6 registers (L1N, L2N, L3N, L12, L23, L31)
    "power_factor": (32084, 2),  # 2 registers (4 bytes, float32)
    "frequency": (32085, 2),  # 2 registers (4 bytes, float32)
    "total_energy": (32106, 2),  # 2 registers (4 bytes, uint32, kWh x 100)
    "alarm_codes": (32090, 2),  # 2 registers
}

# Helper functions
def registers_to_string(registers: List[int]) -> str:
    # Each register is 2 bytes, big endian
    b = bytearray()
    for reg in registers:
        b.extend(reg.to_bytes(2, "big"))
    return b.rstrip(b"\x00").decode("ascii", errors="ignore").strip()

def registers_to_uint32(registers: List[int]) -> int:
    # Two registers, big endian
    return ((registers[0] << 16) | registers[1])

def registers_to_int32(registers: List[int]) -> int:
    # Two registers, big endian, signed
    val = ((registers[0] << 16) | registers[1])
    if val & 0x80000000:
        val -= 0x100000000
    return val

def registers_to_float32(registers: List[int]) -> float:
    import struct
    # Two registers, big endian
    b = bytearray()
    b.extend(registers[0].to_bytes(2, "big"))
    b.extend(registers[1].to_bytes(2, "big"))
    return struct.unpack(">f", b)[0]

def registers_to_uint16_list(registers: List[int]) -> List[int]:
    return registers

# Async Modbus TCP client context
class ModbusTCP:
    def __init__(self, host: str, port: int, unit: int, timeout: float):
        self.host = host
        self.port = port
        self.unit = unit
        self.timeout = timeout
        self.client = None

    async def __aenter__(self):
        self.client = AsyncModbusTcpClient(self.host, port=self.port, timeout=self.timeout)
        await self.client.connect()
        if not self.client.connected:
            raise ConnectionError("Cannot connect to Modbus device")
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.client.close()

    async def read_holding(self, address: int, count: int) -> List[int]:
        try:
            rr = await asyncio.wait_for(
                self.client.read_holding_registers(address, count, unit=self.unit),
                timeout=self.timeout
            )
            if hasattr(rr, "registers") and rr.registers:
                return rr.registers
            raise ModbusException("No registers returned")
        except Exception as e:
            raise HTTPException(status_code=504, detail=f"Modbus read error at {address}: {str(e)}")

    async def write_holding(self, address: int, values: List[int]) -> None:
        try:
            if len(values) == 1:
                await asyncio.wait_for(
                    self.client.write_register(address, values[0], unit=self.unit),
                    timeout=self.timeout
                )
            else:
                await asyncio.wait_for(
                    self.client.write_registers(address, values, unit=self.unit),
                    timeout=self.timeout
                )
        except Exception as e:
            raise HTTPException(status_code=504, detail=f"Modbus write error at {address}: {str(e)}")

# Pydantic models
class ControlCommand(BaseModel):
    # Example: {"registers": [{"address": 42010, "values": [1000]}, {"address": 42020, "values": [1,0]}]}
    registers: List[Dict[str, Any]]

@app.get("/device", response_class=JSONResponse)
async def get_device_info():
    async with ModbusTCP(MODBUS_HOST, MODBUS_PORT, MODBUS_UNIT_ID, MODBUS_TIMEOUT) as modbus:
        model_regs = await modbus.read_holding(*REGISTER_MAP["model"])
        serial_regs = await modbus.read_holding(*REGISTER_MAP["serial_number"])
        fw_regs = await modbus.read_holding(*REGISTER_MAP["firmware_version"])
        rated_power_regs = await modbus.read_holding(*REGISTER_MAP["rated_power"])

        return {
            "model": registers_to_string(model_regs),
            "serial_number": registers_to_string(serial_regs),
            "firmware_version": registers_to_string(fw_regs),
            "rated_power_w": registers_to_uint32(rated_power_regs)
        }

@app.get("/telemetry", response_class=JSONResponse)
async def get_telemetry(
    metrics: Optional[List[str]] = Query(None, description="Optional list of metrics to fetch")
):
    results = {}
    async with ModbusTCP(MODBUS_HOST, MODBUS_PORT, MODBUS_UNIT_ID, MODBUS_TIMEOUT) as modbus:
        # Select which ones to fetch
        targets = REGISTER_MAP.keys() if not metrics else set(metrics) & REGISTER_MAP.keys()
        for k in targets:
            regs = await modbus.read_holding(*REGISTER_MAP[k])
            if k == "model" or k == "serial_number" or k == "firmware_version":
                results[k] = registers_to_string(regs)
            elif k == "rated_power" or k == "total_energy":
                val = registers_to_uint32(regs)
                if k == "total_energy":
                    results[k] = val / 100.0  # kWh
                else:
                    results[k] = val
            elif k == "active_power" or k == "reactive_power":
                results[k] = registers_to_int32(regs)
            elif k == "power_factor" or k == "frequency":
                results[k] = registers_to_float32(regs)
            elif k == "voltages":
                # 6 registers: [L1N, L2N, L3N, L12, L23, L31]
                results[k] = registers_to_uint16_list(regs)
            elif k == "alarm_codes":
                results[k] = registers_to_uint16_list(regs)
            else:
                results[k] = regs
    return results

@app.put("/control", response_class=JSONResponse)
async def control_device(cmd: ControlCommand = Body(...)):
    async with ModbusTCP(MODBUS_HOST, MODBUS_PORT, MODBUS_UNIT_ID, MODBUS_TIMEOUT) as modbus:
        for reg in cmd.registers:
            address = int(reg.get("address"))
            values = reg.get("values")
            if not isinstance(values, list):
                values = [values]
            await modbus.write_holding(address, values)
    return {"status": "success"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HTTP_HOST, port=HTTP_PORT)