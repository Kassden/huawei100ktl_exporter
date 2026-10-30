import json
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from data_collector import DataCollector, HUAWEI_ALARM_BITS
from influxdb_writer import AlarmEventPoint, InfluxDBWriter
from modbus_client import TELEMETRY_MAP


CONTRACT_DIR = Path(__file__).parent / "docs" / "contracts"


def load_contract(name):
    with (CONTRACT_DIR / name).open(encoding="utf-8") as contract_file:
        return json.load(contract_file)


class AlarmEventTests(unittest.TestCase):
    def base_snapshot(self):
        return {
            "alarm_1": 0,
            "alarm_2": 0,
            "alarm_3": 0,
            "highest_priority_alarm_code": 0,
            "number_of_critical_alarms": 0,
            "number_of_major_alarms": 0,
            "number_of_minor_alarms": 0,
            "number_of_warning_alarms": 0,
            "inverter_state": 6,
            "device_state": 0,
        }

    def test_first_snapshot_emits_no_events(self):
        collector = DataCollector()
        telemetry = self.base_snapshot()
        events = collector._build_alarm_events(telemetry, datetime.now(timezone.utc))
        self.assertEqual(events, [])

    def test_alarm_bit_transition_emits_decoded_event(self):
        collector = DataCollector()
        base = self.base_snapshot()

        ts = datetime.now(timezone.utc)
        collector._build_alarm_events(base, ts)

        changed = dict(base)
        changed["alarm_3"] = 1 << 6
        changed["highest_priority_alarm_code"] = 2015
        changed["number_of_warning_alarms"] = 1

        events = collector._build_alarm_events(changed, ts + timedelta(seconds=60))

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.event_type, "alarm_transition")
        self.assertEqual(event.source_field, "alarm_3")
        self.assertEqual(event.source_bit, 6)
        self.assertEqual(event.previous_value, 0.0)
        self.assertEqual(event.current_value, 1.0)
        self.assertEqual(event.alarm_code, 2015)
        self.assertEqual(event.severity, "warning")
        self.assertEqual(event.decoded_alarm_title, "String loss")
        self.assertEqual(event.decoded_alarm_source_name_zh, "组串丢失")
        self.assertEqual(event.decoded_huawei_severity, "提示")

    def test_multiple_changed_alarm_bits_emit_multiple_events(self):
        collector = DataCollector()
        base = self.base_snapshot()

        ts = datetime.now(timezone.utc)
        collector._build_alarm_events(base, ts)

        changed = dict(base)
        changed["alarm_1"] = (1 << 0) | (1 << 2)
        changed["highest_priority_alarm_code"] = 2001
        changed["number_of_major_alarms"] = 2

        events = collector._build_alarm_events(changed, ts + timedelta(seconds=60))
        self.assertEqual([event.alarm_code for event in events], [2001, 2011])
        self.assertEqual([event.source_bit for event in events], [0, 2])
        self.assertTrue(all(event.source_field == "alarm_1" for event in events))

    def test_state_transition_still_emits_event(self):
        collector = DataCollector()
        base = self.base_snapshot()

        ts = datetime.now(timezone.utc)
        collector._build_alarm_events(base, ts)

        changed = dict(base)
        changed["inverter_state"] = 3

        events = collector._build_alarm_events(changed, ts + timedelta(seconds=60))

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "state_transition")
        self.assertEqual(events[0].source_field, "inverter_state")
        self.assertEqual(events[0].previous_value, 6)
        self.assertEqual(events[0].current_value, 3)

    def test_highest_priority_change_falls_back_when_no_bit_explains_it(self):
        collector = DataCollector()
        base = self.base_snapshot()

        ts = datetime.now(timezone.utc)
        collector._build_alarm_events(base, ts)

        changed = dict(base)
        changed["highest_priority_alarm_code"] = 2095
        changed["number_of_major_alarms"] = 1

        events = collector._build_alarm_events(changed, ts + timedelta(seconds=60))

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].source_field, "highest_priority_alarm_code")
        self.assertIsNone(events[0].source_bit)
        self.assertEqual(events[0].alarm_code, 2095)
        self.assertEqual(events[0].severity, "major")

    def test_stable_snapshot_suppresses_duplicates(self):
        collector = DataCollector()
        telemetry = self.base_snapshot()
        ts = datetime.now(timezone.utc)
        collector._build_alarm_events(telemetry, ts)
        events = collector._build_alarm_events(telemetry, ts + timedelta(seconds=60))
        self.assertEqual(events, [])

    def test_alarm_event_point_writes_decoded_metadata_fields(self):
        writer = InfluxDBWriter()
        event = AlarmEventPoint(
            timestamp=datetime(2026, 7, 9, tzinfo=timezone.utc),
            device_id="device-1",
            site_id="site-1",
            event_type="alarm_transition",
            source_field="alarm_3",
            previous_value=0,
            current_value=1,
            alarm_code=2015,
            severity="warning",
            source_bit=6,
            decoded_alarm_title="String loss",
            decoded_alarm_source_name_zh="组串丢失",
            decoded_huawei_severity="提示",
        )

        line_protocol = writer._create_alarm_event_point(event).to_line_protocol()

        self.assertIn("source_field=alarm_3", line_protocol)
        tags, _, fields = line_protocol.partition(" ")
        self.assertIn("event_key=2015:6", tags)
        self.assertNotIn("alarm_code=", tags)
        self.assertNotIn("source_bit=", tags)
        self.assertIn("alarm_code=2015", fields)
        self.assertIn("source_bit=6", fields)
        self.assertIn('decoded_alarm_title="String loss"', line_protocol)
        self.assertIn('decoded_alarm_source_name_zh="组串丢失"', line_protocol)
        self.assertIn('decoded_huawei_severity="提示"', line_protocol)

    def test_same_second_alarm_bits_have_distinct_point_identity(self):
        writer = InfluxDBWriter()
        base = dict(timestamp=datetime(2026, 7, 9, tzinfo=timezone.utc),
                    device_id="device-1", site_id="site-1", event_type="alarm_transition",
                    source_field="alarm_1", previous_value=0, current_value=1,
                    severity="major")
        first = AlarmEventPoint(**base, alarm_code=2001, source_bit=0)
        second = AlarmEventPoint(**base, alarm_code=2011, source_bit=2)
        self.assertNotEqual(writer.alarm_event_tags(first), writer.alarm_event_tags(second))
        for event in (first, second):
            tags = writer.alarm_event_tags(event)
            fields = writer.alarm_event_fields(event)
            self.assertFalse(set(tags) & set(fields))
            self.assertIn(f"event_key={tags['event_key']}", writer._create_alarm_event_point(event).to_line_protocol())

    def test_exporter_alarm_decoder_matches_alarm_bit_contract(self):
        contract = load_contract("huawei-alarm-bits.json")
        decoded_registers = {
            name
            for name, spec in contract["registers"].items()
            if spec["decodedByExporter"]
        }

        self.assertEqual(set(HUAWEI_ALARM_BITS.keys()), decoded_registers)
        self.assertNotIn("alarm_4", HUAWEI_ALARM_BITS)
        self.assertNotIn("alarm_5", HUAWEI_ALARM_BITS)

        contracted_bits = {
            (row["alarmRegister"], row["bit"]): row
            for row in contract["alarmBits"]
            if row["alarmRegister"] in decoded_registers
        }

        actual_bits = {
            (source_field, bit): definition
            for source_field, bits in HUAWEI_ALARM_BITS.items()
            for bit, definition in bits.items()
        }

        self.assertEqual(set(actual_bits.keys()), set(contracted_bits.keys()))
        for key, definition in actual_bits.items():
            row = contracted_bits[key]
            self.assertEqual(definition.source_field, row["alarmRegister"])
            self.assertEqual(definition.bit, row["bit"])
            self.assertEqual(definition.alarm_code, row["code"])
            self.assertEqual(definition.title, row["title"])
            self.assertEqual(definition.source_name_zh, row["sourceNameZh"])
            self.assertEqual(definition.severity, row["severity"])
            self.assertEqual(definition.huawei_severity, row["huaweiSeverity"])

    def test_exporter_telemetry_map_matches_huawei_sun2000_contract(self):
        contract = load_contract("influx-measurements.json")
        huawei_measurement = contract["measurements"]["huawei_sun2000"]

        self.assertEqual(huawei_measurement["defaultName"], "huawei_sun2000")
        self.assertEqual(huawei_measurement["tags"], ["device_id", "site_id"])
        self.assertEqual(huawei_measurement["fields"], list(TELEMETRY_MAP.keys()))

    def test_alarm_event_writer_matches_influx_contract(self):
        contract = load_contract("influx-measurements.json")
        alarm_events = contract["measurements"]["alarm_events"]
        writer = InfluxDBWriter()
        event = AlarmEventPoint(
            timestamp=datetime(2026, 7, 9, tzinfo=timezone.utc),
            device_id="device-1",
            site_id="site-1",
            event_type="alarm_transition",
            source_field="alarm_3",
            previous_value=0,
            current_value=1,
            alarm_code=2015,
            severity="warning",
            source_bit=6,
            decoded_alarm_title="String loss",
            decoded_alarm_source_name_zh="组串丢失",
            decoded_huawei_severity="提示",
        )

        line_protocol = writer._create_alarm_event_point(event).to_line_protocol()

        self.assertEqual(
            alarm_events["tags"],
            ["device_id", "site_id", "event_type", "source_field", "severity"],
        )
        for tag in alarm_events["tags"]:
            self.assertIn(f"{tag}=", line_protocol)
        for field in alarm_events["fields"]:
            self.assertIn(f"{field}=", line_protocol)


if __name__ == "__main__":
    unittest.main()
