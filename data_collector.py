import logging
import asyncio
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from collections import deque
from config import config
from influxdb_writer import AlarmEventPoint, TelemetryPoint, influxdb_writer
from solar_position import solar_position_measurements
from weather_client import build_weather_provider

# Import the modbus client classes
from modbus_client import (
    HuaweiModbusClient, TELEMETRY_MAP, DEVICE_MAP,
    parse_int32_registers, parse_uint32_registers, parse_uint16_register, 
    parse_int16_register, parse_epoch_seconds_registers, parse_string_registers
)

logger = logging.getLogger(__name__)

ALARM_TRANSITION_FIELDS = [
    "alarm_1",
    "alarm_2",
    "alarm_3",
    "highest_priority_alarm_code",
    "number_of_critical_alarms",
    "number_of_major_alarms",
    "number_of_minor_alarms",
    "number_of_warning_alarms",
    "inverter_state",
    "device_state",
]

class DataCollector:
    """Collects telemetry data from solar inverter and batches it for InfluxDB"""
    
    def __init__(self):
        self.scheduler = AsyncIOScheduler()
        self.data_buffer: deque = deque(maxlen=1000)  # Buffer for collected data
        self.alarm_event_buffer: deque = deque(maxlen=2000)
        self.modbus_client = HuaweiModbusClient(
            config.modbus.host,
            config.modbus.port,
            config.modbus.unit_id,
            config.modbus.timeout,
            transport=config.modbus.transport,
            serial_port=config.modbus.serial_port,
            baudrate=config.modbus.baudrate,
            parity=config.modbus.parity,
            bytesize=config.modbus.bytesize,
            stopbits=config.modbus.stopbits,
        )
        self.device_info: Optional[Dict[str, Any]] = None
        self.is_running = False
        self.started_at: Optional[datetime] = None
        self.last_successful_collection_at: Optional[datetime] = None
        self.last_failed_collection_at: Optional[datetime] = None
        self.last_successful_upload_at: Optional[datetime] = None
        self.last_failed_upload_at: Optional[datetime] = None
        self.consecutive_collection_failures = 0
        self.consecutive_upload_failures = 0
        self.dropped_points = 0
        self.dropped_alarm_events = 0
        self.last_alarm_snapshot: Optional[Dict[str, Optional[float]]] = None
        self.weather_provider = build_weather_provider(config.weather)
        
    async def start(self):
        """Start the data collector"""
        if self.is_running:
            logger.warning("Data collector is already running")
            return
            
        try:
            self.scheduler = AsyncIOScheduler()

            # Connect to Modbus device
            await self.modbus_client.connect()
            logger.info("Connected to Modbus device")
            
            # Connect to InfluxDB
            if not await influxdb_writer.connect():
                raise RuntimeError("Failed to connect to InfluxDB")
                
            # Get device information once
            await self._collect_device_info()

            self.started_at = datetime.now(timezone.utc)
            
            # Start scheduled data collection
            self.scheduler.add_job(
                self._collect_and_buffer_data,
                trigger=IntervalTrigger(seconds=config.exporter.collection_interval),
                id='collect_data',
                max_instances=1,
                coalesce=True,
                replace_existing=True,
            )
            
            # Start scheduled batch upload. Sites can opt into faster cloud
            # freshness without changing the Modbus polling cadence.
            upload_interval = (
                config.exporter.upload_interval
                if config.exporter.upload_interval is not None
                else min(config.exporter.collection_interval * 5, 300)
            )
            self.scheduler.add_job(
                self._upload_batch,
                trigger=IntervalTrigger(seconds=upload_interval),
                id='upload_batch',
                max_instances=1,
                coalesce=True,
                replace_existing=True,
            )

            # Collect and upload immediately so commissioning does not wait
            await self._collect_and_buffer_data()
            await self._upload_batch()

            self.scheduler.start()
            self.is_running = True
            logger.info(f"Data collector started (collection interval: {config.exporter.collection_interval}s, upload interval: {upload_interval}s)")
            
        except Exception as e:
            logger.error(f"Failed to start data collector: {e}")
            self.is_running = False
            self.modbus_client.close()
            await influxdb_writer.disconnect()
            raise
    
    async def stop(self):
        """Stop the data collector"""
        if not self.is_running:
            return
            
        try:
            # Upload remaining data
            if self.data_buffer:
                await self._upload_batch()
                
            # Stop scheduler
            if self.scheduler.running:
                self.scheduler.shutdown()
            
            # Close connections
            self.modbus_client.close()
            await influxdb_writer.disconnect()
            
            self.is_running = False
            logger.info("Data collector stopped")
            
        except Exception as e:
            logger.error(f"Error stopping data collector: {e}")
    
    def _parse_device_value(self, regs, data_type):
        """Parse device value based on data type"""
        if data_type == "string":
            return parse_string_registers(regs)
        elif data_type == "int32":
            return parse_int32_registers(regs)
        elif data_type == "uint32":
            return parse_uint32_registers(regs)
        elif data_type == "uint16":
            return parse_uint16_register(regs)
        elif data_type == "int16":
            return parse_int16_register(regs)
        else:
            return regs[0] if len(regs) == 1 else regs
    
    async def _collect_device_info(self):
        """Collect static device information"""
        try:
            device_info = {}
            for key, spec in DEVICE_MAP.items():
                try:
                    regs = await self.modbus_client.read_holding_registers(spec["address"], spec["count"])
                    value = self._parse_device_value(regs, spec["type"])
                    
                    # Apply scaling if specified and value is numeric
                    if isinstance(value, (int, float)) and "scale" in spec:
                        value = round(value * spec["scale"], 6)
                        
                    device_info[key] = value
                except Exception as e:
                    logger.warning(f"Failed to read device info {key}: {e}")
                    device_info[key] = None
                    
            self.device_info = device_info
            logger.info(f"Device info collected: {device_info}")
            
        except Exception as e:
            logger.error(f"Failed to collect device info: {e}")
            self.device_info = {}
    
    def _parse_telemetry_value(self, regs, data_type):
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
    
    async def _collect_telemetry_data(self) -> Dict[str, Any]:
        """Collect current telemetry data from the inverter"""
        telemetry = {}
        failed_count = 0
        
        for key, spec in TELEMETRY_MAP.items():
            try:
                regs = await self.modbus_client.read_holding_registers(spec["address"], spec["count"])
                value = self._parse_telemetry_value(regs, spec["type"])
                
                if value is not None and isinstance(value, (int, float)):
                    # Apply scaling
                    scale = spec.get("scale", 1)
                    value = round(value * scale, 6)
                    
                telemetry[key] = value
                    
            except Exception as e:
                logger.warning(f"Failed to read telemetry {key}: {e}")
                telemetry[key] = None
                failed_count += 1
        
        # Log summary of collection
        total_count = len(TELEMETRY_MAP)
        success_count = total_count - failed_count
        logger.debug(f"Collected {success_count}/{total_count} telemetry points")

        if success_count == 0:
            raise RuntimeError("Failed to collect any telemetry points from inverter")
        
        return telemetry
    
    async def _collect_and_buffer_data(self):
        """Collect telemetry data and add to buffer"""
        try:
            telemetry = await self._collect_telemetry_data()
            collected_at = datetime.now(timezone.utc)
            if self.weather_provider is not None:
                try:
                    telemetry.update(await self.weather_provider.get_measurements(collected_at))
                except Exception as e:
                    logger.warning(f"Weather enrichment failed without blocking telemetry: {e}")
            if config.weather.latitude is not None and config.weather.longitude is not None:
                telemetry.update(
                    solar_position_measurements(
                        collected_at,
                        config.weather.latitude,
                        config.weather.longitude,
                    )
                )
            alarm_events = self._build_alarm_events(telemetry, collected_at)
            
            # Create telemetry point
            point = TelemetryPoint(
                timestamp=collected_at,
                device_id=config.exporter.device_id,
                site_id=config.exporter.site_id,
                measurements=telemetry,
                device_info=self.device_info
            )
            
            # Add to buffer
            if len(self.data_buffer) >= self.data_buffer.maxlen:
                self.dropped_points += 1
                logger.error("Telemetry buffer full; dropping oldest point before appending new data")
            self.data_buffer.append(point)

            for event in alarm_events:
                if len(self.alarm_event_buffer) >= self.alarm_event_buffer.maxlen:
                    self.dropped_alarm_events += 1
                    logger.error("Alarm event buffer full; dropping oldest event before appending")
                self.alarm_event_buffer.append(event)

            self.last_successful_collection_at = point.timestamp
            self.consecutive_collection_failures = 0
            logger.debug(f"Collected telemetry data, buffer size: {len(self.data_buffer)}")
            
            # Upload if buffer is full
            if len(self.data_buffer) >= config.exporter.batch_size:
                await self._upload_batch()
                
        except Exception as e:
            self.last_failed_collection_at = datetime.now(timezone.utc)
            self.consecutive_collection_failures += 1
            logger.error(f"Error collecting data: {e}")
    
    async def _upload_batch(self):
        """Upload batched data to InfluxDB"""
        if not self.data_buffer and not self.alarm_event_buffer:
            logger.debug("No data to upload")
            return True
            
        try:
            telemetry_batch = list(self.data_buffer)
            alarm_event_batch = list(self.alarm_event_buffer)

            telemetry_success = True
            alarm_events_success = True

            if telemetry_batch:
                telemetry_success = await influxdb_writer.write_points(telemetry_batch)
            if alarm_event_batch:
                alarm_events_success = await influxdb_writer.write_alarm_events(alarm_event_batch)

            if telemetry_success and telemetry_batch:
                self.data_buffer.clear()
                logger.info(f"Successfully uploaded batch of {len(telemetry_batch)} data points")
            elif telemetry_batch:
                logger.error(f"Failed to upload batch of {len(telemetry_batch)} data points")

            if alarm_events_success and alarm_event_batch:
                self.alarm_event_buffer.clear()
                logger.info(f"Successfully uploaded batch of {len(alarm_event_batch)} alarm events")
            elif alarm_event_batch:
                logger.error(f"Failed to upload batch of {len(alarm_event_batch)} alarm events")

            success = telemetry_success and alarm_events_success
            if success:
                self.last_successful_upload_at = datetime.now(timezone.utc)
                self.consecutive_upload_failures = 0
            else:
                self.last_failed_upload_at = datetime.now(timezone.utc)
                self.consecutive_upload_failures += 1

            return success
                
        except Exception as e:
            self.last_failed_upload_at = datetime.now(timezone.utc)
            self.consecutive_upload_failures += 1
            logger.error(f"Error uploading batch: {e}")
            return False

    def _build_alarm_events(
        self, telemetry: Dict[str, Any], timestamp: datetime
    ) -> List[AlarmEventPoint]:
        snapshot = {
            field: self._to_float(telemetry.get(field))
            for field in ALARM_TRANSITION_FIELDS
        }

        if self.last_alarm_snapshot is None:
            self.last_alarm_snapshot = snapshot
            return []

        events: List[AlarmEventPoint] = []
        severity = self._calculate_alarm_severity(snapshot)
        alarm_code = int(snapshot.get("highest_priority_alarm_code") or 0)

        for field in ALARM_TRANSITION_FIELDS:
            previous_value = self.last_alarm_snapshot.get(field)
            current_value = snapshot.get(field)
            if previous_value == current_value:
                continue

            events.append(
                AlarmEventPoint(
                    timestamp=timestamp,
                    device_id=config.exporter.device_id,
                    site_id=config.exporter.site_id,
                    event_type="state_transition"
                    if field in {"inverter_state", "device_state"}
                    else "alarm_transition",
                    source_field=field,
                    previous_value=previous_value,
                    current_value=current_value,
                    alarm_code=alarm_code,
                    severity=severity,
                )
            )

        self.last_alarm_snapshot = snapshot
        return events

    @staticmethod
    def _to_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        if isinstance(value, bool):
            return float(int(value))
        if isinstance(value, (int, float)):
            return float(value)
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _calculate_alarm_severity(snapshot: Dict[str, Optional[float]]) -> str:
        critical = int(snapshot.get("number_of_critical_alarms") or 0)
        major = int(snapshot.get("number_of_major_alarms") or 0)
        minor = int(snapshot.get("number_of_minor_alarms") or 0)
        warning = int(snapshot.get("number_of_warning_alarms") or 0)
        highest = int(snapshot.get("highest_priority_alarm_code") or 0)

        if critical > 0:
            return "critical"
        if major > 0 or highest > 0:
            return "major"
        if minor > 0:
            return "minor"
        if warning > 0:
            return "warning"
        return "none"

    def is_collection_fresh(self) -> bool:
        if not self.last_successful_collection_at:
            return False

        age = datetime.now(timezone.utc) - self.last_successful_collection_at
        threshold_seconds = max(
            config.exporter.collection_interval * 2,
            config.exporter.stale_after_seconds,
        )
        return age.total_seconds() <= threshold_seconds

    def is_ready(self) -> bool:
        return (
            self.is_running
            and self.modbus_client.is_connected()
            and influxdb_writer.is_connected()
            and self.is_collection_fresh()
        )
    
    def get_status(self) -> Dict[str, Any]:
        """Get collector status"""
        return {
            "running": self.is_running,
            "ready": self.is_ready(),
            "buffer_size": len(self.data_buffer),
            "alarm_event_buffer_size": len(self.alarm_event_buffer),
            "buffer_max_size": self.data_buffer.maxlen,
            "dropped_points": self.dropped_points,
            "dropped_alarm_events": self.dropped_alarm_events,
            "collection_interval": config.exporter.collection_interval,
            "upload_interval": (
                config.exporter.upload_interval
                if config.exporter.upload_interval is not None
                else min(config.exporter.collection_interval * 5, 300)
            ),
            "batch_size": config.exporter.batch_size,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "last_successful_collection_at": self.last_successful_collection_at.isoformat() if self.last_successful_collection_at else None,
            "last_failed_collection_at": self.last_failed_collection_at.isoformat() if self.last_failed_collection_at else None,
            "last_successful_upload_at": self.last_successful_upload_at.isoformat() if self.last_successful_upload_at else None,
            "last_failed_upload_at": self.last_failed_upload_at.isoformat() if self.last_failed_upload_at else None,
            "collection_fresh": self.is_collection_fresh(),
            "consecutive_collection_failures": self.consecutive_collection_failures,
            "consecutive_upload_failures": self.consecutive_upload_failures,
            "device_info": self.device_info,
            "next_run_times": {
                job.id: job.next_run_time.isoformat() if job.next_run_time else None 
                for job in self.scheduler.get_jobs()
            } if self.scheduler.running else {}
        }
    
    async def force_upload(self) -> bool:
        """Force upload of current buffer"""
        await self._upload_batch()
        return len(self.data_buffer) == 0

# Global data collector instance
data_collector = DataCollector()
