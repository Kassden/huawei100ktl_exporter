import unittest
from datetime import datetime, timezone

from solar_position import solar_position_measurements


class SolarPositionTests(unittest.TestCase):
    def test_hong_kong_midday_has_positive_solar_elevation(self):
        measurements = solar_position_measurements(
            datetime(2026, 5, 29, 4, 0, tzinfo=timezone.utc),
            latitude=22.275001,
            longitude=114.153326,
        )

        self.assertEqual(measurements["solar_daylight_flag"], 1)
        self.assertGreater(measurements["solar_elevation_deg"], 60)
        self.assertGreater(measurements["solar_cos_zenith"], 0)

    def test_hong_kong_midnight_has_no_daylight_flag(self):
        measurements = solar_position_measurements(
            datetime(2026, 5, 28, 16, 0, tzinfo=timezone.utc),
            latitude=22.275001,
            longitude=114.153326,
        )

        self.assertEqual(measurements["solar_daylight_flag"], 0)
        self.assertLess(measurements["solar_elevation_deg"], 0)
        self.assertEqual(measurements["solar_cos_zenith"], 0)

    def test_output_keys_are_stable_for_influx_fields(self):
        measurements = solar_position_measurements(
            datetime(2026, 5, 29, 4, 0, tzinfo=timezone.utc),
            latitude=22.275001,
            longitude=114.153326,
        )

        self.assertEqual(
            set(measurements.keys()),
            {
                "solar_azimuth_deg",
                "solar_elevation_deg",
                "solar_zenith_deg",
                "solar_cos_zenith",
                "solar_daylight_flag",
            },
        )


if __name__ == "__main__":
    unittest.main()
