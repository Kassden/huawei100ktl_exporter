import os
import asyncio
from typing import List, Optional, Dict, Any
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from pymodbus.client.async_tcp import AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException
from pymodbus.pdu import ExceptionResponse
import logging

# Configuration from environment variables
MODBUS_TCP_HOST = os.environ.get("MODBUS_TCP_HOST", "127.0.0.1")
MODBUS_TCP_PORT = int(os.environ.get("MODBUS_TCP_PORT", 502))
MODBUS_UNIT_ID = int(os.environ.get("MODBUS_UNIT_ID", 1))
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", 8080))
MODBUS_TIMEOUT = float(os.environ.get("MODBUS_TIMEOUT", 3.0))
MODBUS_RETRIES = int(os.environ.get("MODBUS_RETRIES", 3))

# Register map
REGISTERS = {
    "model": (30000, 6, "string"),
    "serial_number": (30015, 10, "string"),
    "firmware_version": (30035, 4, "string"),
    "rated_power": (30073, 2, "uint32"),
    "active_power": (32080, 2, "int32"),
    "reactive_power": (32082, 2, "int32"),
    "voltage_a": (32066, 2, "uint32"),
    "voltage_b": (32068, 2, "uint32"),
    "voltage_c": (32070, 2, "uint32"),
    "power_factor": (32084, 2, "int32"),
    "frequency": (32085, 2, "uint32"),
    "total_energy": (32106, 2, "uint32"),
    "alarm_codes": (32090, 2, "uint32"),
}

CONTROL_REGISTERS = {
    "active_power_limit": (42000, 2, "uint32"),
    "reactive_power_limit": (42010, 2, "int32"),
    # Add more control registers as needed
}

# Setup Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("huawei_sun2000_driver")

# FastAPI App
app = FastAPI(
    title="Huawei SUN2000 Solar Inverter Shifu Driver",
    description="HTTP Driver for Huawei SUN2000 Inverters via Modbus-TCP",
    version="1.0.0"
)

# Modbus Connection Pool
class ModbusTCPConnectionPool:
    def __init__(self):
        self.client: Optional[AsyncModbusTcpClient] = None
        self.lock = asyncio.Lock()

    async def get_client(self) -> AsyncModbusTcpClient:
        async with self.lock:
            if self.client is None or not self.client.connected:
                self.client = AsyncModbusTcpClient(
                    MODBUS_TCP_HOST,
                    port=MODBUS_TCP_PORT,
                    timeout=MODBUS_TIMEOUT
                )
                await self.client.connect()
            return self.client

    async def close(self):
        async with self.lock:
            if self.client:
                await self.client.close()
                self.client = None

modbus_pool = ModbusTCPConnectionPool()


# Data Models
class ControlPayload(BaseModel):
    registers: Dict[str, Any]


# Utility Functions

def decode_string(registers: List[int]) -> str:
    # Each register is 2 bytes, combine into a byte string
    b = bytearray()
    for reg in registers:
        b.append((reg >> 8) & 0xFF)
        b.append(reg & 0xFF)
    # Remove trailing nulls and spaces
    return b.decode("ascii", errors="ignore").rstrip('\x00').strip()

def decode_uint32(registers: List[int]) -> int:
    # Combine two registers (high, low)
    if len(registers) < 2:
        return 0
    return (registers[0] << 16) | registers[1]

def decode_int32(registers: List[int]) -> int:
    # Combine two registers and interpret as signed
    if len(registers) < 2:
        return 0
    val = (registers[0] << 16) | registers[1]
    if val & 0x80000000:
        val -= 0x100000000
    return val

def encode_uint32(value: int) -> List[int]:
    return [(value >> 16) & 0xFFFF, value & 0xFFFF]

def encode_int32(value: int) -> List[int]:
    if value < 0:
        value += 0x100000000
    return [(value >> 16) & 0xFFFF, value & 0xFFFF]

def get_register_decode_type(datatype: str):
    if datatype == "string":
        return decode_string
    elif datatype == "uint32":
        return decode_uint32
    elif datatype == "int32":
        return decode_int32
    else:
        return lambda x: x

def get_register_encode_type(datatype: str):
    if datatype == "uint32":
        return encode_uint32
    elif datatype == "int32":
        return encode_int32
    else:
        return lambda x: x

async def modbus_read_holding(client: AsyncModbusTcpClient, address: int, count: int) -> Optional[List[int]]:
    for attempt in range(MODBUS_RETRIES):
        try:
            rr = await client.read_holding_registers(address, count, unit=MODBUS_UNIT_ID)
            if isinstance(rr, ExceptionResponse) or not hasattr(rr, 'registers'):
                logger.error(f"Modbus error at address {address}: {rr}")
                continue
            return rr.registers
        except ModbusException as ex:
            logger.error(f"Modbus read error ({ex}), attempt {attempt+1}/{MODBUS_RETRIES}")
            await asyncio.sleep(0.1)
        except Exception as ex:
            logger.error(f"Unknown error on modbus read: {ex}")
            await asyncio.sleep(0.1)
    return None

async def modbus_write_registers(client: AsyncModbusTcpClient, address: int, values: List[int]) -> bool:
    for attempt in range(MODBUS_RETRIES):
        try:
            if len(values) == 1:
                wr = await client.write_register(address, values[0], unit=MODBUS_UNIT_ID)
            else:
                wr = await client.write_registers(address, values, unit=MODBUS_UNIT_ID)
            if isinstance(wr, ExceptionResponse):
                logger.error(f"Modbus write error at address {address}: {wr}")
                continue
            return True
        except ModbusException as ex:
            logger.error(f"Modbus write error ({ex}), attempt {attempt+1}/{MODBUS_RETRIES}")
            await asyncio.sleep(0.1)
        except Exception as ex:
            logger.error(f"Unknown error on modbus write: {ex}")
            await asyncio.sleep(0.1)
    return False

# API Implementation

@app.get("/device", tags=["Device"])
async def get_device_info():
    """
    Retrieves essential device details including the model, serial number, firmware version, and rated power.
    """
    client = await modbus_pool.get_client()
    out = {}
    for key in ["model", "serial_number", "firmware_version", "rated_power"]:
        addr, count, dtype = REGISTERS[key]
        regs = await modbus_read_holding(client, addr, count)
        if regs is None:
            raise HTTPException(status_code=504, detail=f"Failed to read register {key}")
        decode = get_register_decode_type(dtype)
        out[key] = decode(regs)
    return JSONResponse(out)

@app.get("/telemetry", tags=["Telemetry"])
async def get_telemetry(
    metrics: Optional[List[str]] = Query(None, description="Specific telemetry metrics to return (e.g. active_power, voltage_a, frequency)")
):
    """
    Returns real-time operational data such as active power, reactive power, voltage readings, frequency, total energy generated, and alarm codes.
    Query parameters can be used to filter the data or limit the response to specific metrics.
    """
    client = await modbus_pool.get_client()
    metric_list = metrics if metrics else [
        "active_power", "reactive_power", "voltage_a", "voltage_b", "voltage_c",
        "power_factor", "frequency", "total_energy", "alarm_codes"
    ]
    out = {}
    for key in metric_list:
        if key not in REGISTERS:
            continue
        addr, count, dtype = REGISTERS[key]
        regs = await modbus_read_holding(client, addr, count)
        if regs is None:
            out[key] = None
            continue
        decode = get_register_decode_type(dtype)
        out[key] = decode(regs)
    return JSONResponse(out)

@app.put("/control", tags=["Control"])
async def control_device(payload: ControlPayload):
    """
    Allows remote control by updating configuration settings such as active/reactive power limits and grid support functions.
    The JSON payload should specify the registers and new values.
    """
    client = await modbus_pool.get_client()
    results = {}
    for key, value in payload.registers.items():
        if key not in CONTROL_REGISTERS:
            results[key] = "unknown register"
            continue
        addr, count, dtype = CONTROL_REGISTERS[key]
        encode = get_register_encode_type(dtype)
        values = encode(value)
        ok = await modbus_write_registers(client, addr, values)
        results[key] = "success" if ok else "failed"
    return JSONResponse(results)

@app.on_event("shutdown")
async def shutdown_event():
    await modbus_pool.close()

# FastAPI run for ASGI servers: Uvicorn or Hypercorn externally.
# To run: uvicorn thisfilename:app --host $SERVER_HOST --port $SERVER_PORT