import os
import sys
import logging
import math
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# Import our new components
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from config import config
from modbus_client import (
    HuaweiModbusClient, TELEMETRY_MAP, DEVICE_MAP, CONTROL_MAP, SETTINGS_MAP,
    parse_register_value, build_register_payload
)
from data_collector import data_collector
from influxdb_writer import influxdb_writer

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DEFAULT_LIMITS = {
    "rated_power_kw": 100.0,
    "max_active_power_kw": 100.0,
    "max_apparent_power_kva": 100.0,
    "max_reactive_power_kvar": 100.0,
}

WRITE_BLOCKLIST = {
    "q_u_curve_model": "Blocked by default because curve-mode enum values are not yet modeled for site-safe writes.",
    "cosphi_p_pn_characteristic_curve": "Blocked by default because raw 21-register curve writes are not site-safe through this API.",
    "q_u_characteristic_curve": "Blocked by default because raw 21-register curve writes are not site-safe through this API.",
    "pf_u_characteristic_curve": "Blocked by default because raw 21-register curve writes are not site-safe through this API.",
    "q_p_characteristic_curve": "Blocked by default because raw 21-register curve writes are not site-safe through this API.",
    "grid_standard_code": "Blocked by default because grid-code changes must be commissioned locally.",
    "output_mode": "Blocked by default because the vendor documentation marks this as not intended for normal remote setting on this device.",
    "voltage_level": "Blocked by default because voltage-profile changes must be commissioned locally.",
    "frequency_level": "Blocked by default because frequency-profile changes must be commissioned locally.",
}

COMMAND_TRIGGER_VALUES = {
    "power_on": [1],
    "shutdown": [1],
    "reset": [1],
}

BINARY_SETTINGS = {
    "automatic_power_grid_fault_recovery",
    "power_limit_zero_percent_shutdown",
    "automatic_shutoff_communication_link_disconnection",
    "communication_resumes_automatic_power_on",
    "power_quality_optimization_mode",
    "rcd_enhancement",
    "no_time_work",
    "night_pid_protection",
    "reactive_power_parameter_takes_effect_at_night",
    "afci",
    "mppt_multimodal_scanning",
}

PERCENT_0_TO_100_SETTINGS = {
    "q_u_scheduling_trigger_power_percentage",
    "active_power_percentage_derating",
    "percent_apparent_power",
    "q_u_scheduling_exit_power_percentage",
    "active_power_percentage_control",
    "reactive_power_variation_gradient",
    "active_power_gradient",
    "frequency_modulation_control_power_variation_gradient",
    "fm_control_power_variation_limit",
}

SIGNED_UNIT_INTERVAL_SETTINGS = {
    "power_factor_setting",
    "reactive_power_compensation_qs",
    "reactive_power_compensation_at_night_qs",
}

POSITIVE_UNIT_INTERVAL_SETTINGS = {
    "minimum_pf_limit_for_q_u_curve",
}

ACTIVE_POWER_KW_LIMIT_SETTINGS = {
    "active_power_kw_derating",
    "maximum_active_power",
    "active_power_reference",
}

ACTIVE_POWER_W_LIMIT_SETTINGS = {
    "active_power_fixed_value_derating_w",
}

APPARENT_POWER_KVA_LIMIT_SETTINGS = {
    "maximum_apparent_power",
}

REACTIVE_POWER_KVAR_ABSOLUTE_LIMIT_SETTINGS = {
    "fixed_reactive_power_at_night",
}

REACTIVE_POWER_KVAR_POSITIVE_LIMIT_SETTINGS = {
    "apparent_power_reference",
}

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

class SettingsWriteRequest(BaseModel):
    settings: List[dict] = Field(..., description="List of settings with keys: 'name' and 'value'")

# Global Modbus client
modbus_client = HuaweiModbusClient(
    MODBUS_HOST,
    MODBUS_PORT,
    MODBUS_UNIT_ID,
    timeout=MODBUS_TIMEOUT,
    transport=config.modbus.transport,
    serial_port=config.modbus.serial_port,
    baudrate=config.modbus.baudrate,
    parity=config.modbus.parity,
    bytesize=config.modbus.bytesize,
    stopbits=config.modbus.stopbits,
)

# RTU serial devices cannot be opened twice. Reuse the API client for the
# collector so the exporter holds a single Modbus connection.
data_collector.modbus_client = modbus_client

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
            value = parse_register_value(regs, spec["type"])
            
            # Apply scaling if specified
            if isinstance(value, (int, float)) and "scale" in spec:
                value = round(value * spec["scale"], 6)
                
            result[key] = value
        return JSONResponse(result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def parse_telemetry_value(regs, data_type):
    return parse_register_value(regs, data_type)

def apply_scale(value: Any, spec: Dict[str, Any]) -> Any:
    if isinstance(value, (int, float)) and "scale" in spec:
        return round(value * spec["scale"], 6)
    return value

def _coerce_finite_number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    if not math.isfinite(numeric):
        return None
    return numeric

def _resolve_type_bounds(spec: Dict[str, Any]) -> Dict[str, Optional[float]]:
    scale = spec.get("scale", 1) or 1
    data_type = spec["type"]
    if data_type == "uint16":
        return {"minimum": 0.0, "maximum": 65535.0 * scale}
    if data_type == "int16":
        return {"minimum": -32768.0 * scale, "maximum": 32767.0 * scale}
    if data_type == "uint32":
        return {"minimum": 0.0, "maximum": 4294967295.0 * scale}
    if data_type == "int32":
        return {"minimum": -2147483648.0 * scale, "maximum": 2147483647.0 * scale}
    if data_type == "epoch_seconds":
        return {"minimum": 0.0, "maximum": 4294967295.0}
    return {"minimum": None, "maximum": None}

async def get_device_limits() -> Dict[str, Any]:
    info = dict(data_collector.device_info or {})
    required_fields = {
        "model": "model",
        "rated_power": "rated_power",
        "max_active_power": "max_active_power",
        "max_apparent_power": "max_apparent_power",
        "max_reactive_power_feed_to_grid": "max_reactive_power_feed_to_grid",
        "max_reactive_power_absorb_from_grid": "max_reactive_power_absorb_from_grid",
    }

    missing = [field for field, key in required_fields.items() if info.get(field) is None]
    for field in missing:
        spec = DEVICE_MAP[field]
        try:
            payload = await read_named_register(field, spec)
            info[field] = payload["value"]
        except Exception as exc:
            logger.warning("Unable to refresh device limit field %s: %s", field, exc)

    rated_power_kw = _coerce_finite_number(info.get("rated_power")) or DEFAULT_LIMITS["rated_power_kw"]
    max_active_power_kw = _coerce_finite_number(info.get("max_active_power")) or rated_power_kw or DEFAULT_LIMITS["max_active_power_kw"]
    max_apparent_power_kva = _coerce_finite_number(info.get("max_apparent_power")) or max_active_power_kw or DEFAULT_LIMITS["max_apparent_power_kva"]

    reactive_candidates = [
        abs(candidate)
        for candidate in (
            _coerce_finite_number(info.get("max_reactive_power_feed_to_grid")),
            _coerce_finite_number(info.get("max_reactive_power_absorb_from_grid")),
        )
        if candidate is not None
    ]
    max_reactive_power_kvar = max(reactive_candidates) if reactive_candidates else max_active_power_kw or DEFAULT_LIMITS["max_reactive_power_kvar"]

    return {
        "model": info.get("model"),
        "rated_power_kw": rated_power_kw,
        "max_active_power_kw": max_active_power_kw,
        "max_apparent_power_kva": max_apparent_power_kva,
        "max_reactive_power_kvar": max_reactive_power_kvar,
    }

def describe_write_constraints(name: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    descriptor: Dict[str, Any] = {
        "writable": True,
        "step": spec.get("scale", 1),
    }
    descriptor.update(_resolve_type_bounds(spec))

    if name in WRITE_BLOCKLIST:
        descriptor["writable"] = False
        descriptor["reason"] = WRITE_BLOCKLIST[name]
        return descriptor

    if spec["type"] == "mld":
        descriptor["writable"] = False
        descriptor["reason"] = "Raw multi-register payload writes are blocked by default."
        return descriptor

    if name in COMMAND_TRIGGER_VALUES:
        descriptor["allowed_values"] = COMMAND_TRIGGER_VALUES[name]
        descriptor["minimum"] = COMMAND_TRIGGER_VALUES[name][0]
        descriptor["maximum"] = COMMAND_TRIGGER_VALUES[name][0]
        return descriptor

    if name == "remote_power_scheduling":
        descriptor["allowed_values"] = [1]
        descriptor["minimum"] = 1
        descriptor["maximum"] = 1
        descriptor["reason"] = "Only enabling is allowed through the exporter because disabling can lock remote scheduling."
        return descriptor

    if name in BINARY_SETTINGS:
        descriptor["allowed_values"] = [0, 1]
        descriptor["minimum"] = 0
        descriptor["maximum"] = 1
        return descriptor

    if name in PERCENT_0_TO_100_SETTINGS:
        descriptor["minimum"] = 0.0
        descriptor["maximum"] = 100.0
        return descriptor

    if name in SIGNED_UNIT_INTERVAL_SETTINGS:
        descriptor["minimum"] = -1.0
        descriptor["maximum"] = 1.0
        return descriptor

    if name in POSITIVE_UNIT_INTERVAL_SETTINGS:
        descriptor["minimum"] = 0.0
        descriptor["maximum"] = 1.0
        return descriptor

    if name in ACTIVE_POWER_KW_LIMIT_SETTINGS:
        descriptor["minimum"] = 0.0
        descriptor["maximum_source"] = "max_active_power_kw"
        return descriptor

    if name in ACTIVE_POWER_W_LIMIT_SETTINGS:
        descriptor["minimum"] = 0.0
        descriptor["maximum_source"] = "max_active_power_kw"
        descriptor["maximum_multiplier"] = 1000.0
        return descriptor

    if name in APPARENT_POWER_KVA_LIMIT_SETTINGS:
        descriptor["minimum"] = 0.0
        descriptor["maximum_source"] = "max_apparent_power_kva"
        return descriptor

    if name in REACTIVE_POWER_KVAR_ABSOLUTE_LIMIT_SETTINGS:
        descriptor["absolute_limit_source"] = "max_reactive_power_kvar"
        return descriptor

    if name in REACTIVE_POWER_KVAR_POSITIVE_LIMIT_SETTINGS:
        descriptor["minimum"] = 0.0
        descriptor["maximum_source"] = "max_reactive_power_kvar"
        return descriptor

    return descriptor

async def validate_write_value(name: str, value: Any, spec: Dict[str, Any]) -> Any:
    descriptor = describe_write_constraints(name, spec)
    if not descriptor.get("writable", True):
        raise ValueError(descriptor.get("reason", f"{name} is not writable through this API"))

    if spec["type"] == "mld":
        raise ValueError("Raw multi-register payload writes are blocked by default")

    numeric_value = _coerce_finite_number(value)
    if numeric_value is None:
        raise ValueError(f"{name} must be a finite numeric value")

    allowed_values = descriptor.get("allowed_values")
    if allowed_values is not None and numeric_value not in allowed_values:
        raise ValueError(f"{name} must be one of {allowed_values}")

    minimum = descriptor.get("minimum")
    maximum = descriptor.get("maximum")

    if "absolute_limit_source" in descriptor:
        limits = await get_device_limits()
        absolute_limit = limits[descriptor["absolute_limit_source"]]
        minimum = -absolute_limit
        maximum = absolute_limit
    elif "maximum_source" in descriptor:
        limits = await get_device_limits()
        maximum = limits[descriptor["maximum_source"]]
        maximum *= descriptor.get("maximum_multiplier", 1.0)

    tolerance = 1e-9
    if minimum is not None and numeric_value < minimum - tolerance:
        raise ValueError(f"{name} must be >= {minimum}")
    if maximum is not None and numeric_value > maximum + tolerance:
        raise ValueError(f"{name} must be <= {maximum}")

    step = descriptor.get("step", spec.get("scale", 1)) or 1
    raw_value = numeric_value / step
    if abs(raw_value - round(raw_value)) > 1e-9:
        raise ValueError(f"{name} must align to step {step}")

    return int(round(numeric_value)) if step == 1 else numeric_value

def serialize_register_metadata(name: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": name,
        "address": spec["address"],
        "count": spec["count"],
        "type": spec["type"],
        "scale": spec.get("scale", 1),
        "unit": spec.get("unit"),
        "description": spec.get("description"),
        "validation": describe_write_constraints(name, spec),
    }

async def read_named_register(name: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    regs = await modbus_client.read_holding_registers(spec["address"], spec["count"])
    parsed = parse_register_value(regs, spec["type"])
    value = apply_scale(parsed, spec)
    return {
        "name": name,
        "value": value,
        "registers": regs,
        "address": spec["address"],
        "count": spec["count"],
        "type": spec["type"],
        "unit": spec.get("unit"),
    }

async def write_named_register(name: str, value: Any, spec: Dict[str, Any], read_back: bool = True) -> Dict[str, Any]:
    value = await validate_write_value(name, value, spec)
    regs = build_register_payload(spec, value)
    await modbus_client.write_registers(spec["address"], regs)
    result = {
        "name": name,
        "status": "ok",
        "written_registers": regs,
        "address": spec["address"],
    }
    if read_back and spec["type"] != "mld":
        try:
            result["read_back"] = await read_named_register(name, spec)
        except Exception as exc:
            result["read_back_error"] = str(exc)
    return result

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
            try:
                results.append(await write_named_register(name, value, spec, read_back=name not in {"power_on", "shutdown", "reset"}))
            except ValueError as exc:
                results.append({"name": name, "status": "error", "message": str(exc)})
        return JSONResponse({"results": results})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/control/catalog", summary="List supported remote control commands")
async def get_control_catalog():
    return JSONResponse({"controls": [serialize_register_metadata(name, spec) for name, spec in CONTROL_MAP.items()]})

@app.get("/settings/catalog", summary="List supported remote settings")
async def get_settings_catalog():
    return JSONResponse({"settings": [serialize_register_metadata(name, spec) for name, spec in SETTINGS_MAP.items()]})

@app.get("/settings", summary="Read current inverter settings")
async def get_settings(names: Optional[List[str]] = Query(None)):
    selected_names = names if names else list(SETTINGS_MAP.keys())
    results = {}
    for name in selected_names:
        spec = SETTINGS_MAP.get(name)
        if not spec:
            results[name] = {"error": "Unknown setting"}
            continue
        try:
            results[name] = await read_named_register(name, spec)
        except Exception as exc:
            results[name] = {"name": name, "error": str(exc)}
    return JSONResponse(results)

@app.put("/settings", summary="Write inverter settings")
async def put_settings(payload: SettingsWriteRequest):
    if not config.exporter.enable_control:
        raise HTTPException(status_code=403, detail="Remote control is disabled in this environment")

    results = []
    for item in payload.settings:
        name = item.get("name")
        value = item.get("value")
        if name not in SETTINGS_MAP:
            results.append({"name": name, "status": "error", "message": "Unknown setting"})
            continue
        try:
            results.append(await write_named_register(name, value, SETTINGS_MAP[name], read_back=True))
        except ValueError as exc:
            results.append({"name": name, "status": "error", "message": str(exc)})
        except Exception as exc:
            results.append({"name": name, "status": "error", "message": str(exc)})
    return JSONResponse({"results": results})

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
                "transport": config.modbus.transport,
                "host": config.modbus.host,
                "port": config.modbus.port,
                "unit_id": config.modbus.unit_id,
                "timeout": config.modbus.timeout,
                "serial_port": config.modbus.serial_port,
                "baudrate": config.modbus.baudrate,
                "parity": config.modbus.parity,
                "bytesize": config.modbus.bytesize,
                "stopbits": config.modbus.stopbits,
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
