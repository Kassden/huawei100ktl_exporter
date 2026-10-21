import os
import tempfile
import unittest
from datetime import datetime, timezone

from telemetry_spool import TelemetrySpool


class TelemetrySpoolTests(unittest.TestCase):
    def make_spool(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        path = os.path.join(tmpdir.name, "telemetry-spool.sqlite3")
        spool = TelemetrySpool(
            path,
            max_bytes=100_000_000,
            min_free_bytes=1,
            batch_points=100,
            batch_bytes=262_144,
        ).open()
        self.addCleanup(spool.close)
        return spool

    def test_collection_rolls_back_all_points_and_alarm_baseline(self):
        spool = self.make_spool()
        ts = datetime(2026, 9, 20, tzinfo=timezone.utc)
        valid = dict(source_ts=ts, measurement="huawei_sun2000", site="site", device="device",
                     fields={"active_power": 1.0}, tags={"site_id": "site", "device_id": "device"},
                     line_protocol="huawei_sun2000,device_id=device,site_id=site active_power=1 1790000000")
        invalid = dict(valid, fields={"active_power": float("nan")})

        with self.assertRaises(ValueError):
            spool.enqueue_collection([valid, invalid], {"alarm_1": 1})

        self.assertEqual(spool.stats()["spool_pending"], 0)
        self.assertIsNone(spool.get_state("last_alarm_snapshot"))

    def test_second_writer_cannot_open_same_spool(self):
        spool = self.make_spool()
        other = TelemetrySpool(str(spool.path), min_free_bytes=1)
        with self.assertRaisesRegex(RuntimeError, "already in use"):
            other.open()
        spool.close()
        self.addCleanup(other.close)
        other.open()

    def test_conflicting_payload_does_not_replace_pending_point(self):
        spool = self.make_spool()
        point = dict(source_ts=datetime(2026, 9, 20, tzinfo=timezone.utc), measurement="huawei_sun2000",
                     site="site", device="device", fields={"active_power": 1.0},
                     tags={"site_id": "site", "device_id": "device"},
                     line_protocol="huawei_sun2000,device_id=device,site_id=site active_power=1 1790000000")
        self.assertTrue(spool.enqueue(**point))
        with self.assertRaisesRegex(ValueError, "Conflicting telemetry payload"):
            spool.enqueue(**dict(point, fields={"active_power": 2.0}))
        self.assertEqual(spool.pending_batch()[0].fields["active_power"], 1.0)
        spool.mark_acknowledged(spool.pending_batch())
        spool.mark_drained(spool.acknowledged_batch())
        self.assertFalse(spool.enqueue(**point))
        with self.assertRaisesRegex(ValueError, "Conflicting telemetry payload"):
            spool.enqueue(**dict(point, fields={"active_power": 2.0}))

    def test_spool_round_trips_full_fields_tags_and_line_protocol(self):
        spool = self.make_spool()
        source_ts = datetime(2026, 9, 20, 0, 0, tzinfo=timezone.utc)
        inserted = spool.enqueue(
            source_ts=source_ts,
            measurement="huawei_sun2000",
            site="apac_devops_site",
            device="huawei_sun2000_001",
            fields={"active_power": 0.0, "device_state": 3.0, "serial": "abc"},
            tags={"site_id": "apac_devops_site", "device_id": "huawei_sun2000_001"},
            line_protocol="huawei_sun2000,device_id=huawei_sun2000_001,site_id=apac_devops_site active_power=0 1790000000",
        )

        self.assertTrue(inserted)
        batch = spool.pending_batch()
        self.assertEqual(len(batch), 1)
        self.assertEqual(batch[0].measurement, "huawei_sun2000")
        self.assertEqual(batch[0].fields["active_power"], 0.0)
        self.assertEqual(batch[0].fields["serial"], "abc")
        self.assertEqual(batch[0].tags["device_id"], "huawei_sun2000_001")
        self.assertIn("active_power", batch[0].line_protocol)

    def test_duplicate_immutable_payload_is_idempotent(self):
        spool = self.make_spool()
        kwargs = {
            "source_ts": datetime(2026, 9, 20, 0, 0, tzinfo=timezone.utc),
            "measurement": "huawei_sun2000",
            "site": "site",
            "device": "device",
            "fields": {"active_power": 1.0},
            "tags": {"site_id": "site", "device_id": "device"},
            "line_protocol": "huawei_sun2000,device_id=device,site_id=site active_power=1 1790000000",
        }

        self.assertTrue(spool.enqueue(**kwargs))
        self.assertFalse(spool.enqueue(**kwargs))
        self.assertEqual(spool.stats()["spool_pending"], 1)

    def test_acknowledged_rows_are_deleted_only_after_drain(self):
        spool = self.make_spool()
        spool.enqueue(
            source_ts=datetime(2026, 9, 20, 0, 0, tzinfo=timezone.utc),
            measurement="huawei_sun2000",
            site="site",
            device="device",
            fields={"active_power": 1.0},
            tags={"site_id": "site", "device_id": "device"},
            line_protocol="huawei_sun2000,device_id=device,site_id=site active_power=1 1790000000",
        )
        batch = spool.pending_batch()

        spool.mark_acknowledged(batch)
        self.assertEqual(spool.stats()["spool_acknowledged"], 1)
        spool.mark_drained(batch)
        self.assertEqual(spool.stats()["spool_acknowledged"], 0)
        self.assertEqual(spool.stats()["spool_pending"], 0)

    def test_collector_state_survives_reopen(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        path = os.path.join(tmpdir.name, "telemetry-spool.sqlite3")
        spool = TelemetrySpool(path, min_free_bytes=1).open()
        spool.set_state("last_alarm_snapshot", {"alarm_1": 0, "inverter_state": 3})
        spool.close()

        reopened = TelemetrySpool(path, min_free_bytes=1).open()
        self.addCleanup(reopened.close)
        self.assertEqual(
            reopened.get_state("last_alarm_snapshot"),
            {"alarm_1": 0, "inverter_state": 3},
        )


if __name__ == "__main__":
    unittest.main()
