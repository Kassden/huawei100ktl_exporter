import os
import sys
import logging
from datetime import datetime, timezone
from typing import Optional, List
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# Import our new components
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from config import config
from modbus_client import (
    ModbusTCPClient, TELEMETRY_MAP, DEVICE_MAP, CONTROL_MAP,
    parse_string_registers, parse_int32_registers, parse_uint32_registers,
    parse_uint16_register, parse_int16_register, parse_epoch_seconds_registers,
    build_int32_registers
)
from data_collector import data_collector
from influxdb_writer import influxdb_writer

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Legacy config variables for compatibility
MODBUS_HOST = config.modbus.host
MODBUS_PORT = config.modbus.port
MODBUS_UNIT_ID = config.modbus.unit_id
HTTP_HOST = config.http.host
HTTP_PORT = config.http.port
MODBUS_TIMEOUT = config.modbus.timeout

# FastAPI app
app = FastAPI(title="Huawei SUN2000 DeviceShifu Driver")

class ControlCommand(BaseModel):
    commands: List[dict] = Field(..., description="List of control commands with keys: 'name' (eg. 'active_power_limit'), 'value'")

# Global Modbus client
modbus_client = ModbusTCPClient(MODBUS_HOST, MODBUS_PORT, MODBUS_UNIT_ID, timeout=MODBUS_TIMEOUT)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await modbus_client.connect()
    # Start data collector
    try:
        await data_collector.start()
        logger.info("Data collector started successfully")
    except Exception as e:
        logger.error(f"Failed to start data collector: {e}")
    
    yield
    
    # Shutdown
    modbus_client.close()
    # Stop data collector
    try:
        await data_collector.stop()
        logger.info("Data collector stopped successfully")
    except Exception as e:
        logger.error(f"Error stopping data collector: {e}")

# FastAPI app with lifespan context manager
app = FastAPI(title="Huawei SUN2000 DeviceShifu Driver", lifespan=lifespan)


async def build_health_payload():
    collector_status = data_collector.get_status()
    api_modbus_connected = modbus_client.is_connected()
    collector_modbus_connected = data_collector.modbus_client.is_connected()
    influx_connected = influxdb_writer.is_connected()
    collector_ready = data_collector.is_ready()

    unhealthy_components = []
    if not api_modbus_connected:
        unhealthy_components.append("api_modbus")
    if not collector_modbus_connected:
        unhealthy_components.append("collector_modbus")
    if not collector_status["running"]:
        unhealthy_components.append("data_collector")
    if not influx_connected:
        unhealthy_components.append("influxdb")
    if not collector_status["collection_fresh"]:
        unhealthy_components.append("collection_freshness")

    return {
        "status": "healthy" if not unhealthy_components else "unhealthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "liveness": {
            "status": "alive",
            "uptime_started_at": collector_status["started_at"],
        },
        "readiness": {
            "ready": collector_ready,
            "reason": None if collector_ready else "One or more ingestion dependencies are unhealthy",
        },
        "components": {
            "api_modbus": {
                "status": "connected" if api_modbus_connected else "disconnected",
                "host": MODBUS_HOST,
                "port": MODBUS_PORT,
            },
            "collector_modbus": {
                "status": "connected" if collector_modbus_connected else "disconnected",
                "host": MODBUS_HOST,
                "port": MODBUS_PORT,
            },
            "data_collector": collector_status,
            "influxdb": await influxdb_writer.health_check(),
        },
        "unhealthy_components": unhealthy_components,
    }

@app.get("/device", summary="Get device information")
async def get_device_info():
    try:
        result = {}
        for key, spec in DEVICE_MAP.items():
            regs = await modbus_client.read_holding_registers(spec["address"], spec["count"])
            data_type = spec["type"]
            
            if data_type == "string":
                value = parse_string_registers(regs)
            elif data_type == "int32":
                value = parse_int32_registers(regs)
            elif data_type == "uint32":
                value = parse_uint32_registers(regs)
            elif data_type == "uint16":
                value = parse_uint16_register(regs)
            elif data_type == "int16":
                value = parse_int16_register(regs)
            else:
                value = regs[0] if len(regs) == 1 else regs
            
            # Apply scaling if specified
            if isinstance(value, (int, float)) and "scale" in spec:
                value = round(value * spec["scale"], 6)
                
            result[key] = value
        return JSONResponse(result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def parse_telemetry_value(regs, data_type):
    """Parse telemetry value based on data type"""
    if data_type == "int32":
        return parse_int32_registers(regs)
    elif data_type == "uint32":
        return parse_uint32_registers(regs)
    elif data_type == "uint16":
        return parse_uint16_register(regs)
    elif data_type == "int16":
        return parse_int16_register(regs)
    elif data_type == "epoch_seconds":
        return parse_epoch_seconds_registers(regs)
    else:
        return regs[0] if len(regs) == 1 else regs

@app.get("/telemetry", summary="Get real-time telemetry")
async def get_telemetry(metrics: Optional[List[str]] = Query(None)):
    try:
        result = {}
        telemetry_items = [(m, TELEMETRY_MAP[m]) for m in metrics if m in TELEMETRY_MAP] if metrics else TELEMETRY_MAP.items()
        
        for key, spec in telemetry_items:
            try:
                regs = await modbus_client.read_holding_registers(spec["address"], spec["count"])
                value = parse_telemetry_value(regs, spec["type"])
                
                if value is not None and isinstance(value, (int, float)):
                    # Apply scaling
                    scale = spec.get("scale", 1)
                    value = round(value * scale, 6)
                    
                result[key] = value
            except Exception as e:
                logger.warning(f"Failed to read telemetry {key}: {e}")
                result[key] = None
                
        return JSONResponse(result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/control", summary="Remote control of inverter")
async def control_device(cmd: ControlCommand):
    if not config.exporter.enable_control:
        raise HTTPException(status_code=403, detail="Remote control is disabled in this environment")

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

# New endpoints for data collector management and health checks

@app.get("/live", summary="Liveness check for the exporter service")
async def liveness_check():
    return JSONResponse(
        {
            "status": "alive",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )


@app.get("/ready", summary="Readiness check for the exporter service")
async def readiness_check():
    try:
        health_status = await build_health_payload()
        status_code = 200 if health_status["readiness"]["ready"] else 503
        return JSONResponse(health_status, status_code=status_code)
    except Exception as e:
        return JSONResponse(
            {"status": "error", "error": str(e)},
            status_code=500
        )

@app.get("/health", summary="Health check for the exporter service")
async def health_check():
    """Check health of all components"""
    try:
        health_status = await build_health_payload()
        return JSONResponse(health_status)
        
    except Exception as e:
        return JSONResponse(
            {"status": "error", "error": str(e)}, 
            status_code=500
        )

@app.get("/collector/status", summary="Get data collector status")
async def get_collector_status():
    """Get detailed status of the data collector"""
    try:
        status = data_collector.get_status()
        return JSONResponse(status)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/collector/start", summary="Start the data collector")
async def start_collector():
    """Start the data collector manually"""
    try:
        await data_collector.start()
        return JSONResponse({"message": "Data collector started", "status": "ok"})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/collector/stop", summary="Stop the data collector")
async def stop_collector():
    """Stop the data collector manually"""
    try:
        await data_collector.stop()
        return JSONResponse({"message": "Data collector stopped", "status": "ok"})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/collector/upload", summary="Force upload buffered data")
async def force_upload():
    """Force upload of currently buffered data"""
    try:
        success = await data_collector.force_upload()
        return JSONResponse({
            "message": "Upload completed" if success else "Upload failed",
            "status": "ok" if success else "error",
            "buffer_cleared": success
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/config", summary="Get current configuration")
async def get_config():
    """Get current exporter configuration (sensitive data redacted)"""
    try:
        config_dict = {
            "modbus": {
                "host": config.modbus.host,
                "port": config.modbus.port,
                "unit_id": config.modbus.unit_id,
                "timeout": config.modbus.timeout
            },
            "exporter": {
                "device_id": config.exporter.device_id,
                "site_id": config.exporter.site_id,
                "collection_interval": config.exporter.collection_interval,
                "batch_size": config.exporter.batch_size,
                "retry_attempts": config.exporter.retry_attempts,
                "retry_delay": config.exporter.retry_delay,
                "enable_control": config.exporter.enable_control,
                "stale_after_seconds": config.exporter.stale_after_seconds,
            },
            "influxdb": {
                "url": config.influxdb.url,
                "org": config.influxdb.org,
                "bucket": config.influxdb.bucket,
                "measurement": config.influxdb.measurement,
                "timeout": config.influxdb.timeout,
                "token": "***REDACTED***" if config.influxdb.token else None
            },
            "http": {
                "host": config.http.host,
                "port": config.http.port
            }
        }
        return JSONResponse(config_dict)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HTTP_HOST, port=HTTP_PORT)
