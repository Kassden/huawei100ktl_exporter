import logging
import asyncio
from dataclasses import dataclass
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from collections import deque
from config import config
from influxdb_writer import AlarmEventPoint, TelemetryPoint, influxdb_writer
from telemetry_spool import TelemetrySpool

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

ALARM_BIT_FIELDS = ("alarm_1", "alarm_2", "alarm_3")
STATE_TRANSITION_FIELDS = ("inverter_state", "device_state")

@dataclass(frozen=True)
class HuaweiAlarmBitDefinition:
    source_field: str
    bit: int
    alarm_code: int
    title: str
    source_name_zh: str
    severity: str
    huawei_severity: str


HUAWEI_ALARM_BITS: Dict[str, Dict[int, HuaweiAlarmBitDefinition]] = {
    "alarm_1": {
        0: HuaweiAlarmBitDefinition("alarm_1", 0, 2001, "High input string voltage", "输入组串电压高", "major", "重要"),
        1: HuaweiAlarmBitDefinition("alarm_1", 1, 2002, "DC arc fault", "直流电弧故障", "major", "重要"),
        2: HuaweiAlarmBitDefinition("alarm_1", 2, 2011, "String reverse connection", "组串反接", "major", "重要"),
        3: HuaweiAlarmBitDefinition("alarm_1", 3, 2012, "String current backfeed", "组串电流反灌", "warning", "提示"),
        4: HuaweiAlarmBitDefinition("alarm_1", 4, 2013, "Abnormal string power", "组串功率异常", "warning", "提示"),
        5: HuaweiAlarmBitDefinition("alarm_1", 5, 2021, "AFCI self-check failed", "AFCI自检失败", "major", "重要"),
        6: HuaweiAlarmBitDefinition("alarm_1", 6, 2031, "Grid phase short-circuited to PE", "电网相线对PE短路", "major", "重要"),
        7: HuaweiAlarmBitDefinition("alarm_1", 7, 2032, "Grid power loss", "电网掉电", "major", "重要"),
        8: HuaweiAlarmBitDefinition("alarm_1", 8, 2033, "Grid undervoltage", "电网欠压", "major", "重要"),
        9: HuaweiAlarmBitDefinition("alarm_1", 9, 2034, "Grid overvoltage", "电网过压", "major", "重要"),
        10: HuaweiAlarmBitDefinition("alarm_1", 10, 2035, "Grid voltage imbalance", "电网电压不平衡", "major", "重要"),
        11: HuaweiAlarmBitDefinition("alarm_1", 11, 2036, "Grid overfrequency", "电网过频", "major", "重要"),
        12: HuaweiAlarmBitDefinition("alarm_1", 12, 2037, "Grid underfrequency", "电网欠频", "major", "重要"),
        13: HuaweiAlarmBitDefinition("alarm_1", 13, 2038, "Unstable grid frequency", "电网频率不稳定", "major", "重要"),
        14: HuaweiAlarmBitDefinition("alarm_1", 14, 2039, "Output overcurrent", "输出过流", "major", "重要"),
        15: HuaweiAlarmBitDefinition("alarm_1", 15, 2040, "Excessive DC component in output current", "输出电流直流分量过大", "major", "重要"),
    },
    "alarm_2": {
        0: HuaweiAlarmBitDefinition("alarm_2", 0, 2051, "Abnormal residual current", "残余电流异常", "major", "重要"),
        1: HuaweiAlarmBitDefinition("alarm_2", 1, 2061, "Abnormal system grounding", "系统接地异常", "major", "重要"),
        2: HuaweiAlarmBitDefinition("alarm_2", 2, 2062, "Low insulation resistance", "绝缘阻抗低", "major", "重要"),
        3: HuaweiAlarmBitDefinition("alarm_2", 3, 2063, "High temperature", "温度过高", "minor", "次要"),
        4: HuaweiAlarmBitDefinition("alarm_2", 4, 2064, "Device fault", "设备异常", "major", "重要"),
        5: HuaweiAlarmBitDefinition("alarm_2", 5, 2065, "Upgrade failed or version mismatch", "升级失败或版本不匹配", "minor", "次要"),
        6: HuaweiAlarmBitDefinition("alarm_2", 6, 2066, "License expired", "License到期", "warning", "提示"),
        7: HuaweiAlarmBitDefinition("alarm_2", 7, 61440, "Monitoring unit fault", "监控单元故障", "minor", "次要"),
        8: HuaweiAlarmBitDefinition("alarm_2", 8, 2067, "Power collector fault", "功率采集器故障", "major", "重要"),
        9: HuaweiAlarmBitDefinition("alarm_2", 9, 2068, "Energy storage device fault", "储能设备异常", "minor", "次要"),
        10: HuaweiAlarmBitDefinition("alarm_2", 10, 2070, "Active islanding", "主动孤岛", "major", "重要"),
        11: HuaweiAlarmBitDefinition("alarm_2", 11, 2071, "Passive islanding", "被动孤岛", "major", "重要"),
        12: HuaweiAlarmBitDefinition("alarm_2", 12, 2072, "Transient AC overvoltage", "瞬时交流过压", "major", "重要"),
        13: HuaweiAlarmBitDefinition("alarm_2", 13, 2075, "External device port short circuit", "外部设备端口短路", "warning", "提示"),
        14: HuaweiAlarmBitDefinition("alarm_2", 14, 2077, "Off-grid output overload", "离网输出过载", "major", "重要"),
        15: HuaweiAlarmBitDefinition("alarm_2", 15, 2080, "PV module configuration fault", "电池板配置异常", "major", "重要"),
    },
    "alarm_3": {
        0: HuaweiAlarmBitDefinition("alarm_3", 0, 2081, "Optimizer fault", "优化器故障", "warning", "提示"),
        1: HuaweiAlarmBitDefinition("alarm_3", 1, 2085, "Built-in PID operation fault", "内置PID工作异常", "minor", "次要"),
        2: HuaweiAlarmBitDefinition("alarm_3", 2, 2014, "High input string voltage to ground", "输入组串对地电压高", "major", "重要"),
        3: HuaweiAlarmBitDefinition("alarm_3", 3, 2086, "External fan fault", "外部风扇异常", "major", "重要"),
        4: HuaweiAlarmBitDefinition("alarm_3", 4, 2069, "Energy storage reverse connection", "储能反接", "major", "重要"),
        5: HuaweiAlarmBitDefinition("alarm_3", 5, 2082, "On-grid/off-grid controller fault", "并离网控制器异常", "major", "重要"),
        6: HuaweiAlarmBitDefinition("alarm_3", 6, 2015, "String loss", "组串丢失", "warning", "提示"),
        7: HuaweiAlarmBitDefinition("alarm_3", 7, 2087, "Internal fan fault", "内部风扇异常", "major", "重要"),
        8: HuaweiAlarmBitDefinition("alarm_3", 8, 2088, "DC protection unit fault", "直流保护单元异常", "major", "重要"),
    },
}

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
        self.telemetry_spool: Optional[TelemetrySpool] = None
        self._spool_drain_lock = asyncio.Lock()
        self.spool_last_drain_error: Optional[str] = None
        self.last_write_ack_at: Optional[datetime] = None
        self.last_verified_source_ts: Optional[str] = None
        self.last_cloud_readback_at: Optional[datetime] = None
        
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
            
            if config.spool.enabled:
                self.telemetry_spool = TelemetrySpool(
                    config.spool.path,
                    max_bytes=config.spool.max_bytes,
                    min_free_bytes=config.spool.min_free_bytes,
                    batch_points=config.spool.batch_points,
                    batch_bytes=config.spool.batch_bytes,
                ).open()
                persisted_snapshot = self.telemetry_spool.get_state("last_alarm_snapshot")
                if persisted_snapshot:
                    self.last_alarm_snapshot = {
                        key: self._to_float(value)
                        for key, value in persisted_snapshot.items()
                    }

            # Offline collection must start even when Influx is unavailable.
            if not await influxdb_writer.connect() and not self.telemetry_spool:
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
            if self.telemetry_spool:
                self.telemetry_spool.close()
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
            if self.telemetry_spool:
                self.telemetry_spool.close()
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
            previous_alarm_snapshot = self.last_alarm_snapshot
            alarm_events = self._build_alarm_events(telemetry, collected_at)
            
            # Create telemetry point
            point = TelemetryPoint(
                timestamp=collected_at,
                device_id=config.exporter.device_id,
                site_id=config.exporter.site_id,
                measurements=telemetry,
                device_info=self.device_info
            )
            
            if self.telemetry_spool:
                try:
                    self.telemetry_spool.enqueue_collection(
                        [self._spool_point_payload(point)]
                        + [self._spool_alarm_payload(event) for event in alarm_events],
                        self.last_alarm_snapshot,
                    )
                except Exception:
                    self.last_alarm_snapshot = previous_alarm_snapshot
                    raise
            else:
                # Add to memory buffer
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
            if not self.telemetry_spool and len(self.data_buffer) >= config.exporter.batch_size:
                await self._upload_batch()
                
        except Exception as e:
            self.last_failed_collection_at = datetime.now(timezone.utc)
            self.consecutive_collection_failures += 1
            logger.error(f"Error collecting data: {e}")
    
    async def _upload_batch(self):
        """Upload batched data to InfluxDB"""
        if self.telemetry_spool:
            return await self._drain_spool_batch()

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

    def _enqueue_spool_point(self, point: TelemetryPoint):
        assert self.telemetry_spool is not None
        self.telemetry_spool.enqueue(**self._spool_point_payload(point))

    def _spool_point_payload(self, point: TelemetryPoint) -> Dict[str, Any]:
        influx_point = influxdb_writer._create_point(point)
        return dict(source_ts=point.timestamp, measurement=config.influxdb.measurement,
                    site=point.site_id, device=point.device_id,
                    fields=influxdb_writer.telemetry_fields(point),
                    tags=influxdb_writer.telemetry_tags(point),
                    line_protocol=influx_point.to_line_protocol())

    def _enqueue_spool_alarm_event(self, event: AlarmEventPoint):
        assert self.telemetry_spool is not None
        self.telemetry_spool.enqueue(**self._spool_alarm_payload(event))

    def _spool_alarm_payload(self, event: AlarmEventPoint) -> Dict[str, Any]:
        influx_point = influxdb_writer._create_alarm_event_point(event)
        return dict(source_ts=event.timestamp, measurement=config.influxdb.alarm_events_measurement,
                    site=event.site_id, device=event.device_id,
                    fields=influxdb_writer.alarm_event_fields(event),
                    tags=influxdb_writer.alarm_event_tags(event),
                    line_protocol=influx_point.to_line_protocol())

    async def _drain_spool_batch(self):
        async with self._spool_drain_lock:
            return await self._drain_spool_batch_locked()

    async def _drain_spool_batch_locked(self):
        assert self.telemetry_spool is not None
        acknowledged = self.telemetry_spool.acknowledged_batch()
        acknowledged_verified = await self._verify_and_drain_spool_rows(acknowledged)
        batch = self.telemetry_spool.pending_batch()
        if not batch:
            logger.debug("No spooled data to upload")
            if acknowledged_verified and acknowledged:
                self.spool_last_drain_error = None
                self.consecutive_upload_failures = 0
                self.last_successful_upload_at = datetime.now(timezone.utc)
            return acknowledged_verified
        try:
            if not await influxdb_writer.write_spool_records(batch):
                self.telemetry_spool.mark_pending_error(batch, "Influx write failed")
                self.last_failed_upload_at = datetime.now(timezone.utc)
                self.consecutive_upload_failures += 1
                self.spool_last_drain_error = "Influx write failed"
                return False

            self.telemetry_spool.mark_acknowledged(batch)
            self.last_write_ack_at = datetime.now(timezone.utc)
            verified = await self._verify_and_drain_spool_rows(batch)
            if not verified:
                return False

            self.last_successful_upload_at = datetime.now(timezone.utc)
            if acknowledged_verified:
                self.consecutive_upload_failures = 0
                self.spool_last_drain_error = None
            logger.info("Drained and verified %s spooled telemetry rows", len(batch))
            return acknowledged_verified
        except Exception as e:
            self.last_failed_upload_at = datetime.now(timezone.utc)
            self.consecutive_upload_failures += 1
            self.spool_last_drain_error = str(e)
            logger.error("Error draining telemetry spool: %s", e)
            return False

    async def _verify_and_drain_spool_rows(self, records):
        assert self.telemetry_spool is not None
        if not records:
            return True
        verified = []
        for record in records:
            if await influxdb_writer.verify_spool_records([record]):
                verified.append(record)
            else:
                self.telemetry_spool.mark_verification_error([record], "Influx readback verification failed")
                if getattr(influxdb_writer, "last_readback_error", None):
                    break
        if verified:
            self.telemetry_spool.mark_drained(verified)
            self.last_cloud_readback_at = datetime.now(timezone.utc)
            latest = max(record.source_ts for record in verified)
            self.last_verified_source_ts = max(self.last_verified_source_ts or latest, latest)
        all_verified = len(verified) == len(records)
        if not all_verified:
            self.last_failed_upload_at = datetime.now(timezone.utc)
            self.consecutive_upload_failures += 1
            self.spool_last_drain_error = "Influx readback verification failed"
        return all_verified

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
        decoded_alarm_events = self._build_decoded_alarm_bit_events(
            snapshot,
            timestamp,
        )
        events.extend(decoded_alarm_events)

        for field in STATE_TRANSITION_FIELDS:
            previous_value = self.last_alarm_snapshot.get(field)
            current_value = snapshot.get(field)
            if previous_value == current_value:
                continue

            events.append(
                AlarmEventPoint(
                    timestamp=timestamp,
                    device_id=config.exporter.device_id,
                    site_id=config.exporter.site_id,
                    event_type="state_transition",
                    source_field=field,
                    previous_value=previous_value,
                    current_value=current_value,
                    alarm_code=int(snapshot.get("highest_priority_alarm_code") or 0),
                    severity=self._calculate_alarm_severity(snapshot),
                )
            )

        if not decoded_alarm_events:
            fallback_event = self._build_highest_priority_alarm_event(snapshot, timestamp)
            if fallback_event:
                events.append(fallback_event)

        self.last_alarm_snapshot = snapshot
        return events

    def _build_decoded_alarm_bit_events(
        self,
        snapshot: Dict[str, Optional[float]],
        timestamp: datetime,
    ) -> List[AlarmEventPoint]:
        events: List[AlarmEventPoint] = []

        for field in ALARM_BIT_FIELDS:
            previous_register = self._to_uint16(self.last_alarm_snapshot.get(field))
            current_register = self._to_uint16(snapshot.get(field))
            if previous_register is None or current_register is None:
                continue

            changed_bits = previous_register ^ current_register
            if changed_bits == 0:
                continue

            for bit, definition in HUAWEI_ALARM_BITS.get(field, {}).items():
                bit_mask = 1 << bit
                if not changed_bits & bit_mask:
                    continue

                previous_value = 1.0 if previous_register & bit_mask else 0.0
                current_value = 1.0 if current_register & bit_mask else 0.0
                events.append(
                    AlarmEventPoint(
                        timestamp=timestamp,
                        device_id=config.exporter.device_id,
                        site_id=config.exporter.site_id,
                        event_type="alarm_transition",
                        source_field=field,
                        previous_value=previous_value,
                        current_value=current_value,
                        alarm_code=definition.alarm_code,
                        severity=definition.severity,
                        source_bit=definition.bit,
                        decoded_alarm_title=definition.title,
                        decoded_alarm_source_name_zh=definition.source_name_zh,
                        decoded_huawei_severity=definition.huawei_severity,
                    )
                )

        return events

    def _build_highest_priority_alarm_event(
        self,
        snapshot: Dict[str, Optional[float]],
        timestamp: datetime,
    ) -> Optional[AlarmEventPoint]:
        previous_value = self.last_alarm_snapshot.get("highest_priority_alarm_code")
        current_value = snapshot.get("highest_priority_alarm_code")
        if previous_value == current_value:
            return None

        return AlarmEventPoint(
            timestamp=timestamp,
            device_id=config.exporter.device_id,
            site_id=config.exporter.site_id,
            event_type="alarm_transition",
            source_field="highest_priority_alarm_code",
            previous_value=previous_value,
            current_value=current_value,
            alarm_code=int(current_value or 0),
            severity=self._calculate_alarm_severity(snapshot),
        )

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
    def _to_uint16(value: Any) -> Optional[int]:
        numeric = DataCollector._to_float(value)
        if numeric is None:
            return None
        return int(numeric) & 0xFFFF

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
        storage_healthy = self.telemetry_spool is not None if config.spool.enabled else True
        return (
            self.is_running
            and self.modbus_client.is_connected()
            and (influxdb_writer.is_connected() or self.telemetry_spool is not None)
            and self.is_collection_fresh()
            and storage_healthy
        )
    
    def get_status(self) -> Dict[str, Any]:
        """Get collector status"""
        status = {
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
            "spool_last_drain_error": self.spool_last_drain_error,
            "last_write_ack_at": self.last_write_ack_at.isoformat() if self.last_write_ack_at else None,
            "last_verified_source_ts": self.last_verified_source_ts,
            "last_cloud_readback_at": self.last_cloud_readback_at.isoformat() if self.last_cloud_readback_at else None,
            "next_run_times": {
                job.id: job.next_run_time.isoformat() if job.next_run_time else None 
                for job in self.scheduler.get_jobs()
            } if self.scheduler.running else {}
        }
        if self.telemetry_spool:
            status.update(self.telemetry_spool.stats())
        return status
    
    async def force_upload(self) -> bool:
        """Force upload of current buffer"""
        await self._upload_batch()
        if self.telemetry_spool:
            stats = self.telemetry_spool.stats()
            return all(stats[key] == 0 for key in ("spool_pending", "spool_acknowledged", "spool_quarantined"))
        return len(self.data_buffer) == 0

# Global data collector instance
data_collector = DataCollector()
