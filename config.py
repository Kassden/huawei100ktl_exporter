import os
from typing import Optional
from pydantic import BaseModel, Field
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}

def env_optional_int(name: str) -> Optional[int]:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return None
    parsed = int(value)
    if parsed <= 0:
        raise ValueError(f"{name} must be greater than 0")
    return parsed

def default_modbus_unit_id() -> int:
    configured = os.environ.get("SUN2000_MODBUS_UNIT_ID")
    if configured is not None:
        return int(configured)

    transport = os.environ.get("SUN2000_MODBUS_TRANSPORT", "tcp").strip().lower()
    return 1 if transport == "rtu" else 0

class InfluxDBConfig(BaseModel):
    """InfluxDB configuration"""
    url: str = Field(default_factory=lambda: os.environ.get("INFLUXDB_URL", "http://localhost:8086"))
    token: str = Field(default_factory=lambda: os.environ.get("INFLUXDB_TOKEN", ""))
    org: str = Field(default_factory=lambda: os.environ.get("INFLUXDB_ORG", "solar"))
    bucket: str = Field(default_factory=lambda: os.environ.get("INFLUXDB_BUCKET", "inverters"))
    measurement: str = Field(default_factory=lambda: os.environ.get("INFLUXDB_MEASUREMENT", "huawei_sun2000"))
    alarm_events_measurement: str = Field(
        default_factory=lambda: os.environ.get("INFLUXDB_ALARM_EVENTS_MEASUREMENT", "alarm_events")
    )
    timeout: int = Field(default_factory=lambda: int(os.environ.get("INFLUXDB_TIMEOUT", "30")))

class ModbusConfig(BaseModel):
    """Modbus configuration"""
    transport: str = Field(default_factory=lambda: os.environ.get("SUN2000_MODBUS_TRANSPORT", "tcp").strip().lower())
    host: str = Field(default_factory=lambda: os.environ.get("SUN2000_MODBUS_HOST", "127.0.0.1"))
    port: int = Field(default_factory=lambda: int(os.environ.get("SUN2000_MODBUS_PORT", "502")))
    unit_id: int = Field(default_factory=default_modbus_unit_id)
    timeout: float = Field(default_factory=lambda: float(os.environ.get("SUN2000_MODBUS_TIMEOUT", "5.0")))
    serial_port: Optional[str] = Field(default_factory=lambda: os.environ.get("SUN2000_SERIAL_PORT"))
    baudrate: int = Field(default_factory=lambda: int(os.environ.get("SUN2000_SERIAL_BAUDRATE", "9600")))
    parity: str = Field(default_factory=lambda: os.environ.get("SUN2000_SERIAL_PARITY", "N").strip().upper())
    bytesize: int = Field(default_factory=lambda: int(os.environ.get("SUN2000_SERIAL_BYTESIZE", "8")))
    stopbits: int = Field(default_factory=lambda: int(os.environ.get("SUN2000_SERIAL_STOPBITS", "1")))

class ExporterConfig(BaseModel):
    """Main exporter configuration"""
    device_id: str = Field(default_factory=lambda: os.environ.get("DEVICE_ID", "inverter_001"))
    site_id: str = Field(default_factory=lambda: os.environ.get("SITE_ID", "site_001"))
    collection_interval: int = Field(default_factory=lambda: int(os.environ.get("COLLECTION_INTERVAL", "60")))  # seconds
    upload_interval: Optional[int] = Field(default_factory=lambda: env_optional_int("UPLOAD_INTERVAL"))
    batch_size: int = Field(default_factory=lambda: int(os.environ.get("BATCH_SIZE", "10")))
    retry_attempts: int = Field(default_factory=lambda: int(os.environ.get("RETRY_ATTEMPTS", "3")))
    retry_delay: int = Field(default_factory=lambda: int(os.environ.get("RETRY_DELAY", "5")))  # seconds
    enable_control: bool = Field(default_factory=lambda: env_bool("EXPORTER_ENABLE_CONTROL", False))
    stale_after_seconds: int = Field(default_factory=lambda: int(os.environ.get("EXPORTER_STALE_AFTER_SECONDS", "180")))
    
class HTTPConfig(BaseModel):
    """HTTP server configuration"""
    host: str = Field(default_factory=lambda: os.environ.get("HTTP_HOST", "0.0.0.0"))
    port: int = Field(default_factory=lambda: int(os.environ.get("HTTP_PORT", "8080")))

class AppConfig(BaseModel):
    """Application configuration"""
    influxdb: InfluxDBConfig = Field(default_factory=InfluxDBConfig)
    modbus: ModbusConfig = Field(default_factory=ModbusConfig)
    exporter: ExporterConfig = Field(default_factory=ExporterConfig)
    http: HTTPConfig = Field(default_factory=HTTPConfig)

# Global config instance
config = AppConfig()
