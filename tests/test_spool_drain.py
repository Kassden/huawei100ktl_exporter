import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, Mock, patch
from influxdb_client import WritePrecision

import data_collector
from config import config
from data_collector import DataCollector
from influxdb_writer import AlarmEventPoint, TelemetryPoint, InfluxDBWriter
from telemetry_spool import TelemetrySpool, SpoolRecord


class SpoolDrainTests(unittest.TestCase):
    def make_collector(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        collector = DataCollector()
        collector.telemetry_spool = TelemetrySpool(
            os.path.join(tmpdir.name, "telemetry-spool.sqlite3"),
            max_bytes=100_000_000,
            min_free_bytes=1,
        ).open()
        self.addCleanup(collector.telemetry_spool.close)
        return collector

    def test_collector_starts_with_spool_when_influx_is_offline(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        spool_path = os.path.join(tmpdir.name, "telemetry-spool.sqlite3")
        collector = DataCollector()

        async def run():
            with patch.object(config.spool, "enabled", True), patch.object(config.spool, "path", spool_path), \
                 patch.object(config.spool, "min_free_bytes", 1), \
                 patch.object(collector.modbus_client, "connect", new=AsyncMock()), \
                 patch.object(collector.modbus_client, "close"), \
                 patch.object(collector, "_collect_device_info", new=AsyncMock()), \
                 patch.object(collector, "_collect_and_buffer_data", new=AsyncMock()), \
                 patch.object(collector, "_upload_batch", new=AsyncMock()), \
                 patch.object(data_collector.influxdb_writer, "connect", new=AsyncMock(return_value=False)), \
                 patch.object(data_collector.influxdb_writer, "disconnect", new=AsyncMock()):
                await collector.start()
                self.assertTrue(collector.is_running)
                self.assertIsNotNone(collector.telemetry_spool)
                await collector.stop()

        asyncio.run(run())

    def test_offline_collection_survives_restart_and_replays_source_time(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        path = os.path.join(tmpdir.name, "telemetry-spool.sqlite3")
        source = DataCollector()
        source.telemetry_spool = TelemetrySpool(path, min_free_bytes=1).open()

        with patch.object(source, "_collect_telemetry_data", new=AsyncMock(return_value={"active_power": 12.5})), \
             patch.object(data_collector.influxdb_writer, "write_spool_records", new=AsyncMock(return_value=False)):
            asyncio.run(source._collect_and_buffer_data())
            self.assertEqual(source.telemetry_spool.stats()["spool_pending"], 1)
            original_ts = source.telemetry_spool.pending_batch()[0].source_ts
            self.assertFalse(asyncio.run(source._drain_spool_batch()))
        source.telemetry_spool.conn.execute("UPDATE telemetry_spool SET next_attempt_at = NULL")
        source.telemetry_spool.close()

        recovered = DataCollector()
        recovered.telemetry_spool = TelemetrySpool(path, min_free_bytes=1).open()
        self.addCleanup(recovered.telemetry_spool.close)
        self.assertEqual(recovered.telemetry_spool.pending_batch()[0].source_ts, original_ts)
        with patch.object(data_collector.influxdb_writer, "write_spool_records", new=AsyncMock(return_value=True)) as write, \
             patch.object(data_collector.influxdb_writer, "verify_spool_records", new=AsyncMock(return_value=True)):
            self.assertTrue(asyncio.run(recovered._drain_spool_batch()))
        self.assertEqual(write.await_args.args[0][0].source_ts, original_ts)
        self.assertEqual(recovered.last_verified_source_ts, original_ts)
        self.assertEqual(recovered.telemetry_spool.stats()["spool_pending"], 0)

    def test_collector_enqueues_telemetry_and_alarm_events(self):
        collector = self.make_collector()
        ts = datetime(2026, 9, 20, 0, 0, tzinfo=timezone.utc)

        collector._enqueue_spool_point(
            TelemetryPoint(
                timestamp=ts,
                device_id="device",
                site_id="site",
                measurements={"active_power": 12.5, "flag": True, "bad": None},
                device_info={"model": "SUN2000"},
            )
        )
        collector._enqueue_spool_alarm_event(
            AlarmEventPoint(
                timestamp=ts,
                device_id="device",
                site_id="site",
                event_type="alarm_transition",
                source_field="alarm_3",
                previous_value=0,
                current_value=1,
                alarm_code=2015,
                severity="warning",
                source_bit=6,
                decoded_alarm_title="String loss",
            )
        )

        batch = collector.telemetry_spool.pending_batch()
        self.assertEqual(len(batch), 2)
        measurements = {record.measurement for record in batch}
        self.assertEqual(measurements, {"huawei_sun2000", "alarm_events"})
        self.assertTrue(any("decoded_alarm_title" in record.fields for record in batch))
        telemetry = next(record for record in batch if record.measurement == "huawei_sun2000")
        self.assertEqual(telemetry.fields["flag"], 1.0)

    def test_spool_drain_deletes_only_after_write_and_readback(self):
        collector = self.make_collector()
        collector._enqueue_spool_point(
            TelemetryPoint(
                timestamp=datetime(2026, 9, 20, 0, 0, tzinfo=timezone.utc),
                device_id="device",
                site_id="site",
                measurements={"active_power": 12.5},
            )
        )

        with patch.object(data_collector.influxdb_writer, "write_spool_records", new=AsyncMock(return_value=True)), \
             patch.object(data_collector.influxdb_writer, "verify_spool_records", new=AsyncMock(return_value=True)):
            result = asyncio.run(collector._drain_spool_batch())

        self.assertTrue(result)
        self.assertEqual(collector.telemetry_spool.stats()["spool_pending"], 0)
        self.assertIsNotNone(collector.last_verified_source_ts)

    def test_spool_drain_preserves_acknowledged_rows_when_readback_fails(self):
        collector = self.make_collector()
        collector._enqueue_spool_point(
            TelemetryPoint(
                timestamp=datetime(2026, 9, 20, 0, 0, tzinfo=timezone.utc),
                device_id="device",
                site_id="site",
                measurements={"active_power": 12.5},
            )
        )

        with patch.object(data_collector.influxdb_writer, "write_spool_records", new=AsyncMock(return_value=True)), \
             patch.object(data_collector.influxdb_writer, "verify_spool_records", new=AsyncMock(return_value=False)):
            result = asyncio.run(collector._drain_spool_batch())

        self.assertFalse(result)
        stats = collector.telemetry_spool.stats()
        self.assertEqual(stats["spool_pending"], 0)
        self.assertEqual(stats["spool_acknowledged"], 1)
        self.assertEqual(collector.spool_last_drain_error, "Influx readback verification failed")

    def test_restart_verifies_acknowledged_row_without_resending(self):
        collector = self.make_collector()
        collector._enqueue_spool_point(TelemetryPoint(
            timestamp=datetime(2026, 9, 20, tzinfo=timezone.utc), device_id="device", site_id="site",
            measurements={"active_power": 12.5},
        ))
        spool = collector.telemetry_spool
        spool.mark_acknowledged(spool.pending_batch())
        spool.close()
        collector.telemetry_spool = TelemetrySpool(str(spool.path), min_free_bytes=1).open()
        self.addCleanup(collector.telemetry_spool.close)

        with patch.object(data_collector.influxdb_writer, "write_spool_records", new=AsyncMock()) as write, \
             patch.object(data_collector.influxdb_writer, "verify_spool_records", new=AsyncMock(return_value=True)):
            result = asyncio.run(collector._drain_spool_batch())

        self.assertTrue(result)
        write.assert_not_called()
        self.assertEqual(collector.telemetry_spool.stats()["spool_acknowledged"], 0)

    def test_old_unverified_row_does_not_block_new_telemetry(self):
        collector = self.make_collector()
        start = datetime(2026, 9, 20, tzinfo=timezone.utc)
        for ts in (start, start + timedelta(minutes=1)):
            collector._enqueue_spool_point(TelemetryPoint(
                timestamp=ts, device_id="device", site_id="site", measurements={"active_power": 12.5},
            ))
        collector.telemetry_spool.mark_acknowledged(collector.telemetry_spool.pending_batch()[:1])

        with patch.object(data_collector.influxdb_writer, "write_spool_records", new=AsyncMock(return_value=True)) as write, \
             patch.object(data_collector.influxdb_writer, "verify_spool_records", new=AsyncMock(side_effect=[False, True])):
            result = asyncio.run(collector._drain_spool_batch())

        self.assertFalse(result)
        write.assert_awaited_once()
        stats = collector.telemetry_spool.stats()
        self.assertEqual(stats["spool_acknowledged"], 1)
        self.assertEqual(stats["spool_pending"], 0)

    def test_alarm_bits_in_same_sample_have_distinct_point_identities(self):
        collector = self.make_collector()
        ts = datetime(2026, 9, 20, tzinfo=timezone.utc)
        for code, bit in ((2001, 0), (2002, 1)):
            collector._enqueue_spool_alarm_event(AlarmEventPoint(
                timestamp=ts, device_id="device", site_id="site", event_type="alarm_transition",
                source_field="alarm_1", previous_value=0, current_value=1,
                alarm_code=code, severity="major", source_bit=bit,
            ))
        self.assertEqual(len(collector.telemetry_spool.pending_batch()), 2)


class ReadbackTests(unittest.TestCase):
    def test_spooled_line_protocol_is_written_with_second_precision(self):
        writer = InfluxDBWriter()
        writer.client = Mock()
        writer.write_api = Mock()
        writer._connected = True
        record = SpoolRecord(1, "2026-09-20T00:00:00Z", "huawei_sun2000", "site", "device",
                             {"active_power": 12.5}, {"site_id": "site", "device_id": "device"},
                             "huawei_sun2000,device_id=device,site_id=site active_power=12.5 1790000000", "hash", 0)
        self.assertTrue(asyncio.run(writer.write_spool_records([record])))
        self.assertEqual(writer.write_api.write.call_args.kwargs["write_precision"], WritePrecision.S)

    def test_readback_requires_matching_value_and_source_timestamp(self):
        ts = datetime(2026, 9, 20, tzinfo=timezone.utc)
        record = SpoolRecord(1, ts.isoformat().replace("+00:00", "Z"), "huawei_sun2000", "site", "device",
                             {"active_power": 12.5}, {"site_id": "site", "device_id": "device"}, "lp", "hash", 0)

        class Row:
            def __init__(self, value, when):
                self.value, self.when = value, when

            def get_field(self):
                return "active_power"

            def get_value(self):
                return self.value

            def get_time(self):
                return self.when

        class Table:
            def __init__(self, row):
                self.records = [row]

        class Query:
            def __init__(self, row):
                self.row = row

            def query(self, *, query, org):
                self_test.assertIn('r["device_id"] == "device"', query)
                return [Table(self.row)]

        self_test = self
        writer = InfluxDBWriter()
        self.assertFalse(asyncio.run(writer._verify_single_spool_record(Query(Row(13.5, ts)), record)))
        self.assertFalse(asyncio.run(writer._verify_single_spool_record(Query(Row(12.5, ts + timedelta(seconds=1))), record)))
        self.assertTrue(asyncio.run(writer._verify_single_spool_record(Query(Row(12.5, ts)), record)))


if __name__ == "__main__":
    unittest.main()
