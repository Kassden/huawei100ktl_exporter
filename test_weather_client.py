import unittest
from datetime import datetime, timezone

from config import WeatherConfig
from weather_client import OpenMeteoCurrentWeatherClient, WeatherSnapshot


class WeatherClientTests(unittest.TestCase):
    def test_weather_config_defaults_are_disabled_and_current_resolution(self):
        config = WeatherConfig()

        self.assertFalse(config.enabled)
        self.assertEqual(config.provider, "open_meteo")
        self.assertEqual(config.refresh_interval_seconds, 900)
        self.assertEqual(config.max_stale_seconds, 3600)

    def test_open_meteo_current_response_normalizes_to_telemetry_fields(self):
        snapshot = OpenMeteoCurrentWeatherClient.from_response(
            {
                "current": {
                    "time": "2026-05-29T12:15",
                    "interval": 900,
                    "temperature_2m": 31.4,
                    "relative_humidity_2m": 74,
                    "apparent_temperature": 37.1,
                    "is_day": 1,
                    "precipitation": 0.0,
                    "rain": 0.0,
                    "weather_code": 3,
                    "cloud_cover": 61,
                    "wind_speed_10m": 14.2,
                    "wind_direction_10m": 110,
                    "wind_gusts_10m": 26.5,
                }
            }
        )

        self.assertTrue(snapshot.available)
        self.assertEqual(snapshot.interval_seconds, 900)
        measurements = snapshot.to_measurements(
            datetime(2026, 5, 29, 12, 16, tzinfo=timezone.utc),
            max_stale_seconds=3600,
        )
        self.assertEqual(measurements["weather_available"], 1)
        self.assertEqual(measurements["weather_temperature_2m_c"], 31.4)
        self.assertEqual(measurements["weather_relative_humidity_percent"], 74)
        self.assertEqual(measurements["weather_interval_seconds"], 900)
        self.assertEqual(measurements["weather_stale_seconds"], 60)

    def test_missing_current_payload_returns_unavailable(self):
        snapshot = OpenMeteoCurrentWeatherClient.from_response({})

        self.assertFalse(snapshot.available)
        self.assertEqual(snapshot.to_measurements(datetime.now(timezone.utc), 3600), {"weather_available": 0})

    def test_stale_snapshot_omits_weather_values(self):
        snapshot = WeatherSnapshot(
            available=True,
            observed_at=datetime(2026, 5, 29, 12, 0, tzinfo=timezone.utc),
            interval_seconds=900,
            values={"weather_temperature_2m_c": 30.0},
        )

        measurements = snapshot.to_measurements(
            datetime(2026, 5, 29, 14, 0, tzinfo=timezone.utc),
            max_stale_seconds=3600,
        )

        self.assertEqual(measurements["weather_available"], 0)
        self.assertNotIn("weather_temperature_2m_c", measurements)
        self.assertEqual(measurements["weather_stale_seconds"], 7200)


if __name__ == "__main__":
    unittest.main()
