import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from influxdb_writer import AlarmEventPoint, InfluxDBWriter
from scripts.repair_alarm_spool import repair
from telemetry_spool import TelemetrySpool


class AlarmSpoolRepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "spool.sqlite3"
        self.spool = TelemetrySpool(str(self.db), min_free_bytes=1).open()
        self.addCleanup(self.spool.close)
        self.writer = InfluxDBWriter()

    def enqueue_legacy(self, code, bit):
        event = AlarmEventPoint(
            timestamp=datetime(2026, 10, 21, tzinfo=timezone.utc),
            device_id="device", site_id="site", event_type="alarm_transition",
            source_field="alarm_1", previous_value=0, current_value=1,
            alarm_code=code, source_bit=bit, severity="major",
            decoded_alarm_title="Test alarm",
        )
        tags = self.writer.alarm_event_tags(event)
        tags.pop("event_key")
        tags["alarm_code"] = str(code)
        tags["source_bit"] = str(bit)
        fields = self.writer.alarm_event_fields(event)
        self.spool.enqueue(source_ts=event.timestamp, measurement="alarm_events",
                           site="site", device="device", tags=tags, fields=fields,
                           line_protocol="alarm_events,alarm_code=%s,source_bit=%s alarm_code=%s,source_bit=%s 1792540800" % (code, bit, code, bit))

    def test_mixed_rows_backup_repair_and_repeat_are_lossless(self):
        self.enqueue_legacy(2001, 0)
        self.enqueue_legacy(2011, 2)
        self.spool.enqueue(source_ts=datetime(2026, 10, 21, tzinfo=timezone.utc),
                           measurement="huawei_sun2000", site="site", device="device",
                           tags={"site_id": "site", "device_id": "device"},
                           fields={"active_power": 1.0}, line_protocol="huawei_sun2000 active_power=1 1792540800")
        before = self.spool.conn.execute("SELECT id,source_ts,fields_json FROM telemetry_spool ORDER BY id").fetchall()
        backup = Path(self.tmp.name) / "backup.sqlite3"
        result = repair(self.spool, apply=True, backup=backup)
        self.assertEqual(result["changed"], 2)
        backed_up = sqlite3.connect(backup)
        try:
            self.assertEqual(backed_up.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(backed_up.execute("SELECT count(*) FROM telemetry_spool").fetchone()[0], 3)
        finally:
            backed_up.close()
        after = self.spool.conn.execute("SELECT id,source_ts,fields_json,tags_json,line_protocol FROM telemetry_spool ORDER BY id").fetchall()
        self.assertEqual([(x[0], x[1], x[2]) for x in before], [(x[0], x[1], x[2]) for x in after])
        self.assertEqual(len({json.loads(row[3]).get("event_key") for row in after[:2]}), 2)
        for row in after[:2]:
            self.assertFalse(set(json.loads(row[2])) & set(json.loads(row[3])))
            self.assertIn("event_key=", row[4])
        self.assertEqual(repair(self.spool, apply=True, backup=backup)["changed"], 0)

    def test_existing_backup_blocks_mutation(self):
        self.enqueue_legacy(2001, 0)
        backup = Path(self.tmp.name) / "backup.sqlite3"
        backup.write_text("preserve")
        with self.assertRaises(FileExistsError):
            repair(self.spool, apply=True, backup=backup)
        self.assertEqual(backup.read_text(), "preserve")
        self.assertIn("alarm_code", json.loads(self.spool.conn.execute("SELECT tags_json FROM telemetry_spool").fetchone()[0]))

    def test_identity_conflict_rolls_back_and_preserves_backup(self):
        self.enqueue_legacy(2001, 0)
        row = self.spool.conn.execute("SELECT * FROM telemetry_spool").fetchone()
        from scripts.repair_alarm_spool import repaired_row
        identity_hash = repaired_row(row)[4]
        self.spool.conn.execute("INSERT INTO point_identity(identity_hash,payload_hash,first_seen_at) VALUES(?,?,?)",
                                (identity_hash, "conflicting-payload", row["created_at"]))
        backup = Path(self.tmp.name) / "backup.sqlite3"
        with self.assertRaisesRegex(ValueError, "identity collision"):
            repair(self.spool, apply=True, backup=backup)
        self.assertTrue(backup.exists())
        backed_up = sqlite3.connect(backup)
        try:
            self.assertEqual(backed_up.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        finally:
            backed_up.close()
        self.assertIn("alarm_code", json.loads(self.spool.conn.execute("SELECT tags_json FROM telemetry_spool").fetchone()[0]))


if __name__ == "__main__":
    unittest.main()
