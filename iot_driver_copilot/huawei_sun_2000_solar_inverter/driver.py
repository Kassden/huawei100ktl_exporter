import os
import asyncio
from typing import List, Optional, Dict
from fastapi import FastAPI, HTTPException, Request, Query, Body
from pydantic import BaseModel
from starlette.responses import JSONResponse
from pymodbus.client.async_tcp import AsyncModbusTCPClient
from pymodbus.payload import BinaryPayloadDecoder
from pymodbus.constants import Endian

# --- Environment Variables ---
MODBUS_HOST = os.getenv("MODBUS_HOST", "127.0.0.1")
MODBUS_PORT = int(os.getenv("MODBUS_PORT", "502"))
MODBUS_UNIT = int(os.getenv("MODBUS_UNIT", "1"))
HTTP_SERVER_HOST = os.getenv("HTTP_SERVER_HOST", "0.0.0.0")
HTTP_SERVER_PORT = int(os.getenv("HTTP_SERVER_PORT", "8080"))
MODBUS_TIMEOUT = float(os.getenv("MODBUS_TIMEOUT", "5.0"))
MODBUS_RETRIES = int(os.getenv("MODBUS_RETRIES", "3"))

# --- Register Map ---
REGISTERS = {
    'model': {'address': 30000, 'length': 6, 'type': 'string'},
    'serial_number': {'address': 30015, 'length': 10, 'type': 'string'},
    'firmware_version': {'address': 30035, 'length': 6, 'type': 'string'},
    'rated_power': {'address': 30073, 'length': 2, 'type': 'uint32', 'scale': 1},
    'active_power': {'address': 32080, 'length': 2, 'type': 'int32', 'scale': 0.01},
    'reactive_power': {'address': 32082, 'length': 2, 'type': 'int32', 'scale': 0.01},
    'voltages': {'address': 32066, 'length': 6, 'type': 'uint16_list', 'scale': 0.1},  # Uab/Ubc/Uca/Ia/Ib/Ic
    'power_factor': {'address': 32084, 'length': 2, 'type': 'float32', 'scale': 0.001},
    'frequency': {'address': 32085, 'length': 2, 'type': 'float32', 'scale': 0.01},
    'total_energy': {'address': 32106, 'length': 2, 'type': 'uint32', 'scale': 1.0},
    'alarm_codes': {'address': 32090, 'length': 4, 'type': 'uint16_list'},
}

# --- FastAPI Init ---
app = FastAPI(
    title="Huawei SUN2000 Solar Inverter HTTP Driver",
    description="Driver for Huawei SUN2000 Inverter exposing Modbus data via HTTP",
    version="1.0.0"
)

# --- Utility Functions ---


async def modbus_tcp_client():
    client = AsyncModbusTCPClient(
        host=MODBUS_HOST,
        port=MODBUS_PORT,
        timeout=MODBUS_TIMEOUT,
    )
    await client.start()
    return client


async def read_holding_registers(address: int, count: int) -> Optional[List[int]]:
    for attempt in range(MODBUS_RETRIES):
        try:
            client = await modbus_tcp_client()
            rr = await client.protocol.read_holding_registers(address, count, unit=MODBUS_UNIT)
            await client.stop()
            if not rr.isError():
                return rr.registers
        except Exception:
            await asyncio.sleep(0.5)
    return None


def decode_string(registers: List[int]) -> str:
    bytes_arr = b''
    for reg in registers:
        bytes_arr += reg.to_bytes(2, 'big')
    return bytes_arr.decode('ascii', errors='ignore').strip('\x00 ').strip()


def decode_uint32(registers: List[int]) -> int:
    return (registers[0] << 16) | registers[1]


def decode_int32(registers: List[int]) -> int:
    val = (registers[0] << 16) | registers[1]
    if val & 0x80000000:
        val = -((val ^ 0xFFFFFFFF) + 1)
    return val


def decode_float32(registers: List[int]) -> float:
    decoder = BinaryPayloadDecoder.fromRegisters(registers, byteorder=Endian.Big)
    return decoder.decode_32bit_float()


def decode_uint16_list(registers: List[int], scale=1.0) -> List[float]:
    return [reg * scale for reg in registers]


def build_telemetry_response(regs: Dict[str, dict], query: Optional[List[str]] = None) -> dict:
    telemetry = {}
    for key, info in regs.items():
        if query and key not in query:
            continue
        regs_raw = asyncio.run(read_holding_registers(info['address'], info['length']))
        if regs_raw is None:
            telemetry[key] = None
            continue
        if info['type'] == 'string':
            telemetry[key] = decode_string(regs_raw)
        elif info['type'] == 'uint32':
            val = decode_uint32(regs_raw)
            telemetry[key] = val * info.get('scale', 1)
        elif info['type'] == 'int32':
            val = decode_int32(regs_raw)
            telemetry[key] = val * info.get('scale', 1)
        elif info['type'] == 'float32':
            val = decode_float32(regs_raw)
            telemetry[key] = val * info.get('scale', 1)
        elif info['type'] == 'uint16_list':
            telemetry[key] = decode_uint16_list(regs_raw, info.get('scale', 1.0))
    return telemetry

# --- Schemas ---


class ControlPayload(BaseModel):
    writes: Dict[str, int]


# --- API Endpoints ---


@app.get("/device", tags=["Device Info"], response_class=JSONResponse)
async def get_device_info():
    try:
        result = {}
        model_regs = await read_holding_registers(REGISTERS['model']['address'], REGISTERS['model']['length'])
        serial_regs = await read_holding_registers(REGISTERS['serial_number']['address'], REGISTERS['serial_number']['length'])
        fw_regs = await read_holding_registers(REGISTERS['firmware_version']['address'], REGISTERS['firmware_version']['length'])
        rated_regs = await read_holding_registers(REGISTERS['rated_power']['address'], REGISTERS['rated_power']['length'])

        if not all([model_regs, serial_regs, fw_regs, rated_regs]):
            raise HTTPException(status_code=500, detail="Read failed")

        result["model"] = decode_string(model_regs)
        result["serial_number"] = decode_string(serial_regs)
        result["firmware_version"] = decode_string(fw_regs)
        result["rated_power"] = decode_uint32(rated_regs)

        return JSONResponse(content=result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/telemetry", tags=["Telemetry"], response_class=JSONResponse)
async def get_telemetry(
    metrics: Optional[str] = Query(None, description="Comma separated list of metrics to query"),
):
    try:
        query_keys = None
        if metrics:
            query_keys = [m.strip() for m in metrics.split(",") if m.strip() in REGISTERS]
        telemetry = {}
        for key, info in REGISTERS.items():
            if query_keys and key not in query_keys:
                continue
            regs_raw = await read_holding_registers(info['address'], info['length'])
            if regs_raw is None:
                telemetry[key] = None
                continue
            if info['type'] == 'string':
                telemetry[key] = decode_string(regs_raw)
            elif info['type'] == 'uint32':
                telemetry[key] = decode_uint32(regs_raw) * info.get('scale', 1)
            elif info['type'] == 'int32':
                telemetry[key] = decode_int32(regs_raw) * info.get('scale', 1)
            elif info['type'] == 'float32':
                telemetry[key] = decode_float32(regs_raw) * info.get('scale', 1)
            elif info['type'] == 'uint16_list':
                telemetry[key] = decode_uint16_list(regs_raw, info.get('scale', 1.0))
        return JSONResponse(content=telemetry)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/control", tags=["Control"], response_class=JSONResponse)
async def put_control(payload: ControlPayload = Body(...)):
    try:
        # For each key in payload, write to the relevant register
        writes = payload.writes
        results = {}
        client = await modbus_tcp_client()
        for regname, value in writes.items():
            # Only allow known writable registers (e.g., those in 42000+)
            if not regname.startswith("reg_"):
                continue
            addr = int(regname[4:])
            rr = await client.protocol.write_register(addr, value, unit=MODBUS_UNIT)
            if rr.isError():
                results[regname] = "failed"
            else:
                results[regname] = "ok"
        await client.stop()
        return JSONResponse(content={"result": results})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- Startup Event ---

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=HTTP_SERVER_HOST, port=HTTP_SERVER_PORT)