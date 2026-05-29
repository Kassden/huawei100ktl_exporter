import unittest
from datetime import datetime, timedelta, timezone

from data_collector import DataCollector
from weather_client import CachedWeatherProvider, WeatherSnapshot


class FakeWeatherClient:
    def __init__(self, snapshots):
        self.snapshots = list(snapshots)
        self.calls = 0

    def fetch_current(self):
        self.calls += 1
        return self.snapshots.pop(0)


class WeatherEnrichmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_weather_cache_reuses_snapshot_within_provider_interval(self):
        observed_at = datetime(2026, 5, 29, 12, 0, tzinfo=timezone.utc)
        client = FakeWeatherClient(
            [
                WeatherSnapshot(
                    available=True,
                    observed_at=observed_at,
                    interval_seconds=900,
                    values={"weather_temperature_2m_c": 31.0},
                )
            ]
        )
        provider = CachedWeatherProvider(client, refresh_interval_seconds=900, max_stale_seconds=3600)

        first = await provider.get_measurements(observed_at + timedelta(seconds=60))
        second = await provider.get_measurements(observed_at + timedelta(seconds=120))

        self.assertEqual(client.calls, 1)
        self.assertEqual(first["weather_temperature_2m_c"], 31.0)
        self.assertEqual(second["weather_temperature_2m_c"], 31.0)
        self.assertEqual(second["weather_stale_seconds"], 120)

    async def test_provider_refreshes_after_returned_interval(self):
        observed_at = datetime(2026, 5, 29, 12, 0, tzinfo=timezone.utc)
        client = FakeWeatherClient(
            [
                WeatherSnapshot(
                    available=True,
                    observed_at=observed_at,
                    interval_seconds=900,
                    values={"weather_temperature_2m_c": 31.0},
                ),
                WeatherSnapshot(
                    available=True,
                    observed_at=observed_at + timedelta(minutes=15),
                    interval_seconds=900,
                    values={"weather_temperature_2m_c": 32.0},
                ),
            ]
        )
        provider = CachedWeatherProvider(client, refresh_interval_seconds=900, max_stale_seconds=3600)

        await provider.get_measurements(observed_at)
        refreshed = await provider.get_measurements(observed_at + timedelta(seconds=900))

        self.assertEqual(client.calls, 2)
        self.assertEqual(refreshed["weather_temperature_2m_c"], 32.0)

    async def test_collection_adds_weather_without_blocking_alarm_logic(self):
        collector = DataCollector()
        observed_at = datetime.now(timezone.utc)
        collector.weather_provider = CachedWeatherProvider(
            FakeWeatherClient(
                [
                    WeatherSnapshot(
                        available=True,
                        observed_at=observed_at,
                        interval_seconds=900,
                        values={"weather_cloud_cover_percent": 45},
                    )
                ]
            ),
            refresh_interval_seconds=900,
            max_stale_seconds=3600,
        )

        async def collect_telemetry():
            return {
                "highest_priority_alarm_code": 0,
                "number_of_critical_alarms": 0,
                "number_of_major_alarms": 0,
                "number_of_minor_alarms": 0,
                "number_of_warning_alarms": 0,
                "inverter_state": 6,
                "device_state": 0,
            }

        collector._collect_telemetry_data = collect_telemetry
        await collector._collect_and_buffer_data()

        self.assertEqual(len(collector.data_buffer), 1)
        point = collector.data_buffer[0]
        self.assertEqual(point.measurements["weather_available"], 1)
        self.assertEqual(point.measurements["weather_cloud_cover_percent"], 45)


if __name__ == "__main__":
    unittest.main()
