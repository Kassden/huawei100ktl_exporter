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

class InfluxDBConfig(BaseModel):
    """InfluxDB configuration"""
    url: str = Field(default_factory=lambda: os.environ.get("INFLUXDB_URL", "http://localhost:8086"))
    token: str = Field(default_factory=lambda: os.environ.get("INFLUXDB_TOKEN", ""))
    org: str = Field(default_factory=lambda: os.environ.get("INFLUXDB_ORG", "solar"))
    bucket: str = Field(default_factory=lambda: os.environ.get("INFLUXDB_BUCKET", "inverters"))
    measurement: str = Field(default_factory=lambda: os.environ.get("INFLUXDB_MEASUREMENT", "huawei_sun2000"))
    timeout: int = Field(default_factory=lambda: int(os.environ.get("INFLUXDB_TIMEOUT", "30")))

class ModbusConfig(BaseModel):
    """Modbus configuration"""
    host: str = Field(default_factory=lambda: os.environ.get("SUN2000_MODBUS_HOST", "127.0.0.1"))
    port: int = Field(default_factory=lambda: int(os.environ.get("SUN2000_MODBUS_PORT", "502")))
    unit_id: int = Field(default_factory=lambda: int(os.environ.get("SUN2000_MODBUS_UNIT_ID", "1")))
    timeout: float = Field(default_factory=lambda: float(os.environ.get("SUN2000_MODBUS_TIMEOUT", "5.0")))

class ExporterConfig(BaseModel):
    """Main exporter configuration"""
    device_id: str = Field(default_factory=lambda: os.environ.get("DEVICE_ID", "inverter_001"))
    site_id: str = Field(default_factory=lambda: os.environ.get("SITE_ID", "site_001"))
    collection_interval: int = Field(default_factory=lambda: int(os.environ.get("COLLECTION_INTERVAL", "60")))  # seconds
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
