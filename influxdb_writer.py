import logging
import asyncio
import math
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from dataclasses import dataclass, asdict
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS
from influxdb_client.rest import ApiException
from config import config
from telemetry_spool import SpoolRecord

logger = logging.getLogger(__name__)

@dataclass
class TelemetryPoint:
    """Represents a telemetry data point"""
    timestamp: datetime
    device_id: str
    site_id: str
    measurements: Dict[str, Any]
    device_info: Optional[Dict[str, Any]] = None

@dataclass
class AlarmEventPoint:
    """Represents an alarm or state transition event"""
    timestamp: datetime
    device_id: str
    site_id: str
    event_type: str
    source_field: str
    previous_value: Optional[float]
    current_value: Optional[float]
    alarm_code: int
    severity: str
    source_bit: Optional[int] = None
    decoded_alarm_title: Optional[str] = None
    decoded_alarm_source_name_zh: Optional[str] = None
    decoded_huawei_severity: Optional[str] = None

class InfluxDBWriter:
    """Async InfluxDB client for writing solar inverter telemetry"""
    
    def __init__(self):
        self.client = None
        self.write_api = None
        self._connected = False
        self.last_readback_error: Optional[str] = None
        
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
            ready = await asyncio.to_thread(self.client.ping)
            if ready:
                self.write_api = self.client.write_api(write_options=SYNCHRONOUS)
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

    def telemetry_tags(self, telemetry_point: TelemetryPoint) -> Dict[str, str]:
        tags = {
            "device_id": telemetry_point.device_id,
            "site_id": telemetry_point.site_id,
        }
        if telemetry_point.device_info:
            for key, value in telemetry_point.device_info.items():
                if value is not None:
                    tags[f"device_{key}"] = str(value)
        return tags

    def telemetry_fields(self, telemetry_point: TelemetryPoint) -> Dict[str, Any]:
        return {
            key: float(value) if isinstance(value, (int, float)) else str(value)
            for key, value in telemetry_point.measurements.items()
            if value is not None
        }

    def _create_alarm_event_point(self, event: AlarmEventPoint) -> Point:
        """Convert alarm event to an InfluxDB Point"""
        point = (
            Point(config.influxdb.alarm_events_measurement)
            .tag("device_id", event.device_id)
            .tag("site_id", event.site_id)
            .tag("event_type", event.event_type)
            .tag("source_field", event.source_field)
            .tag("severity", event.severity)
            .tag("event_key", self.alarm_event_key(event.alarm_code, event.source_bit))
            .time(event.timestamp, WritePrecision.S)
        )
        point = point.field("alarm_code", float(event.alarm_code))
        if event.previous_value is not None:
            point = point.field("previous_value", float(event.previous_value))
        if event.current_value is not None:
            point = point.field("current_value", float(event.current_value))
        if event.source_bit is not None:
            point = point.field("source_bit", float(event.source_bit))
        if event.decoded_alarm_title:
            point = point.field("decoded_alarm_title", event.decoded_alarm_title)
        if event.decoded_alarm_source_name_zh:
            point = point.field(
                "decoded_alarm_source_name_zh",
                event.decoded_alarm_source_name_zh,
            )
        if event.decoded_huawei_severity:
            point = point.field("decoded_huawei_severity", event.decoded_huawei_severity)

        return point

    def alarm_event_tags(self, event: AlarmEventPoint) -> Dict[str, str]:
        tags = {
            "device_id": event.device_id,
            "site_id": event.site_id,
            "event_type": event.event_type,
            "source_field": event.source_field,
            "severity": event.severity,
            "event_key": self.alarm_event_key(event.alarm_code, event.source_bit),
        }
        return tags

    @staticmethod
    def alarm_event_key(alarm_code: int, source_bit: Optional[int]) -> str:
        return f"{alarm_code}:{source_bit if source_bit is not None else 'none'}"

    def alarm_event_fields(self, event: AlarmEventPoint) -> Dict[str, Any]:
        fields: Dict[str, Any] = {"alarm_code": float(event.alarm_code)}
        if event.previous_value is not None:
            fields["previous_value"] = float(event.previous_value)
        if event.current_value is not None:
            fields["current_value"] = float(event.current_value)
        if event.source_bit is not None:
            fields["source_bit"] = float(event.source_bit)
        if event.decoded_alarm_title:
            fields["decoded_alarm_title"] = event.decoded_alarm_title
        if event.decoded_alarm_source_name_zh:
            fields["decoded_alarm_source_name_zh"] = event.decoded_alarm_source_name_zh
        if event.decoded_huawei_severity:
            fields["decoded_huawei_severity"] = event.decoded_huawei_severity
        return fields

    async def write_spool_records(self, records: List[SpoolRecord]) -> bool:
        if not records:
            return True
        if not self.is_connected():
            logger.warning("Not connected to InfluxDB, attempting to reconnect...")
            if not await self.connect():
                return False
        try:
            await asyncio.to_thread(
                self.write_api.write,
                bucket=config.influxdb.bucket,
                org=config.influxdb.org,
                record=[record.line_protocol for record in records],
                write_precision=WritePrecision.S,
            )
            logger.info("Synchronously wrote %s spooled points to InfluxDB", len(records))
            return True
        except ApiException as e:
            logger.error(f"InfluxDB API error while writing spooled records: {e}")
            return False
        except Exception as e:
            logger.error(f"Error writing spooled records to InfluxDB: {e}")
            return False

    async def verify_spool_records(self, records: List[SpoolRecord]) -> bool:
        if not records:
            return True
        if not self.client and not await self.connect():
            self.last_readback_error = "InfluxDB unavailable"
            return False
        query_api = self.client.query_api()
        for record in records:
            if not await self._verify_single_spool_record(query_api, record):
                return False
        return True

    async def _verify_single_spool_record(self, query_api, record: SpoolRecord) -> bool:
        self.last_readback_error = None
        source_ts = datetime.fromisoformat(record.source_ts.replace("Z", "+00:00"))
        start = source_ts.replace(microsecond=0).timestamp() - 1
        stop = source_ts.replace(microsecond=0).timestamp() + 1
        start_text = datetime.fromtimestamp(start, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        stop_text = datetime.fromtimestamp(stop, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        tag_filters = " ".join(
            f'|> filter(fn: (r) => r["{self._escape_flux(key)}"] == "{self._escape_flux(value)}")'
            for key, value in record.tags.items()
        )
        field_filter = " or ".join(
            f'r._field == "{self._escape_flux(field)}"' for field in record.fields.keys()
        )
        query = f'''
          from(bucket: "{self._escape_flux(config.influxdb.bucket)}")
            |> range(start: {start_text}, stop: {stop_text})
            |> filter(fn: (r) => r._measurement == "{self._escape_flux(record.measurement)}")
            {tag_filters}
            |> filter(fn: (r) => {field_filter})
        '''
        try:
            rows = await asyncio.to_thread(query_api.query, query=query, org=config.influxdb.org)
            found_fields = {}
            for table in rows:
                for item in table.records:
                    item_time = item.get_time()
                    if item_time and int(item_time.timestamp()) == int(source_ts.timestamp()):
                        found_fields[item.get_field()] = item.get_value()
            return all(
                field in found_fields and self._field_matches(expected, found_fields[field])
                for field, expected in record.fields.items()
            )
        except Exception as e:
            self.last_readback_error = str(e)
            logger.warning("Cloud readback verification failed for spool row %s: %s", record.id, e)
            return False

    @staticmethod
    def _escape_flux(value: str) -> str:
        return str(value).replace("\\", "\\\\").replace('"', '\\"')

    @staticmethod
    def _field_matches(expected: Any, observed: Any) -> bool:
        if isinstance(expected, bool):
            return isinstance(observed, bool) and observed == expected
        if isinstance(expected, (int, float)) and not isinstance(expected, bool):
            return isinstance(observed, (int, float)) and not isinstance(observed, bool) and math.isclose(
                float(expected), float(observed), rel_tol=1e-6, abs_tol=1e-6
            )
        return expected == observed
    
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

    async def write_alarm_events(
        self, alarm_events: List[AlarmEventPoint], retry_count: int = 0
    ) -> bool:
        """Write alarm transition events to InfluxDB"""
        if not alarm_events:
            return True

        if not self.is_connected():
            logger.warning("Not connected to InfluxDB, attempting to reconnect...")
            if not await self.connect():
                return False

        try:
            points = [self._create_alarm_event_point(event) for event in alarm_events]
            self.write_api.write(
                bucket=config.influxdb.bucket,
                org=config.influxdb.org,
                record=points,
            )
            logger.info(f"Successfully wrote {len(points)} alarm events to InfluxDB")
            return True
        except ApiException as e:
            logger.error(f"InfluxDB API error while writing alarm events: {e}")
            return False
        except Exception as e:
            logger.error(f"Error writing alarm events to InfluxDB: {e}")

            if retry_count < config.exporter.retry_attempts:
                logger.info(
                    "Retrying alarm event write (attempt %s/%s)",
                    retry_count + 1,
                    config.exporter.retry_attempts,
                )
                await asyncio.sleep(config.exporter.retry_delay)
                return await self.write_alarm_events(alarm_events, retry_count + 1)
            return False
    
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
