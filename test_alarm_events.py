import unittest
from datetime import datetime, timezone, timedelta

from data_collector import DataCollector


class AlarmEventTests(unittest.TestCase):
    def test_first_snapshot_emits_no_events(self):
        collector = DataCollector()
        telemetry = {
            "highest_priority_alarm_code": 0,
            "number_of_critical_alarms": 0,
            "number_of_major_alarms": 0,
            "number_of_minor_alarms": 0,
            "number_of_warning_alarms": 0,
            "inverter_state": 6,
            "device_state": 0,
        }
        events = collector._build_alarm_events(telemetry, datetime.now(timezone.utc))
        self.assertEqual(events, [])

    def test_transition_emits_events_and_severity(self):
        collector = DataCollector()
        base = {
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

        ts = datetime.now(timezone.utc)
        collector._build_alarm_events(base, ts)

        changed = dict(base)
        changed["highest_priority_alarm_code"] = 2051
        changed["number_of_major_alarms"] = 1
        changed["inverter_state"] = 3

        events = collector._build_alarm_events(changed, ts + timedelta(seconds=60))
        self.assertGreaterEqual(len(events), 3)
        self.assertTrue(any(event.source_field == "highest_priority_alarm_code" for event in events))
        self.assertTrue(any(event.source_field == "inverter_state" for event in events))
        self.assertTrue(all(event.severity == "major" for event in events))

    def test_stable_snapshot_suppresses_duplicates(self):
        collector = DataCollector()
        telemetry = {
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
        ts = datetime.now(timezone.utc)
        collector._build_alarm_events(telemetry, ts)
        events = collector._build_alarm_events(telemetry, ts + timedelta(seconds=60))
        self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
