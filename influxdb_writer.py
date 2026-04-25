import logging
import asyncio
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from dataclasses import dataclass, asdict
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import ASYNCHRONOUS
from influxdb_client.rest import ApiException
from config import config

logger = logging.getLogger(__name__)

@dataclass
class TelemetryPoint:
    """Represents a telemetry data point"""
    timestamp: datetime
    device_id: str
    site_id: str
    measurements: Dict[str, Any]
    device_info: Optional[Dict[str, Any]] = None

class InfluxDBWriter:
    """Async InfluxDB client for writing solar inverter telemetry"""
    
    def __init__(self):
        self.client = None
        self.write_api = None
        self._connected = False
        
    async def connect(self) -> bool:
        """Establish connection to InfluxDB"""
        try:
            if not config.influxdb.token:
                logger.error("InfluxDB token not configured")
                return False
                
            self.client = InfluxDBClient(
                url=config.influxdb.url,
                token=config.influxdb.token,
                org=config.influxdb.org,
                timeout=config.influxdb.timeout * 1000  # Convert to milliseconds
            )
            
            # Test connection
            ready = self.client.ping()
            if ready:
                self.write_api = self.client.write_api(write_options=ASYNCHRONOUS)
                self._connected = True
                logger.info(f"Connected to InfluxDB at {config.influxdb.url}")
                return True
            else:
                logger.error("InfluxDB ping failed")
                return False
                
        except Exception as e:
            logger.error(f"Failed to connect to InfluxDB: {e}")
            return False
    
    async def disconnect(self):
        """Close InfluxDB connection"""
        if self.client:
            try:
                self.client.close()
                self._connected = False
                logger.info("InfluxDB connection closed")
            except Exception as e:
                logger.error(f"Error closing InfluxDB connection: {e}")
    
    def is_connected(self) -> bool:
        """Check if connected to InfluxDB"""
        return self._connected and self.client is not None
    
    def _create_point(self, telemetry_point: TelemetryPoint) -> Point:
        """Convert telemetry point to InfluxDB Point"""
        point = Point(config.influxdb.measurement) \
            .tag("device_id", telemetry_point.device_id) \
            .tag("site_id", telemetry_point.site_id) \
            .time(telemetry_point.timestamp, WritePrecision.S)
        
        # Add device info as tags if available
        if telemetry_point.device_info:
            for key, value in telemetry_point.device_info.items():
                if value is not None:
                    point = point.tag(f"device_{key}", str(value))
        
        # Add measurements as fields
        for key, value in telemetry_point.measurements.items():
            if value is not None:
                if isinstance(value, (int, float)):
                    point = point.field(key, float(value))
                else:
                    point = point.field(key, str(value))
        
        return point
    
    async def write_points(self, telemetry_points: List[TelemetryPoint], retry_count: int = 0) -> bool:
        """Write multiple telemetry points to InfluxDB"""
        if not self.is_connected():
            logger.warning("Not connected to InfluxDB, attempting to reconnect...")
            if not await self.connect():
                return False
        
        try:
            points = [self._create_point(tp) for tp in telemetry_points]
            
            # Write points to InfluxDB
            success = self.write_api.write(
                bucket=config.influxdb.bucket,
                org=config.influxdb.org,
                record=points
            )
            
            logger.info(f"Successfully wrote {len(points)} points to InfluxDB")
            return True
            
        except ApiException as e:
            logger.error(f"InfluxDB API error: {e}")
            return False
        except Exception as e:
            logger.error(f"Error writing to InfluxDB: {e}")
            
            # Retry logic
            if retry_count < config.exporter.retry_attempts:
                logger.info(f"Retrying write operation (attempt {retry_count + 1}/{config.exporter.retry_attempts})")
                await asyncio.sleep(config.exporter.retry_delay)
                return await self.write_points(telemetry_points, retry_count + 1)
            
            return False
    
    async def write_single_point(self, telemetry_point: TelemetryPoint) -> bool:
        """Write a single telemetry point to InfluxDB"""
        return await self.write_points([telemetry_point])
    
    async def health_check(self) -> Dict[str, Any]:
        """Check InfluxDB connection health"""
        if not self.client:
            return {"status": "disconnected", "error": "No client initialized"}
        
        try:
            ready = self.client.ping()
            if ready:
                return {"status": "healthy", "url": config.influxdb.url, "org": config.influxdb.org}
            else:
                return {"status": "unhealthy", "error": "Ping failed"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

# Global InfluxDB writer instance
influxdb_writer = InfluxDBWriter()
