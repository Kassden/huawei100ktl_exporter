import logging
import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import requests
from config import WeatherConfig

logger = logging.getLogger(__name__)

OPEN_METEO_CURRENT_FIELDS = [
    "temperature_2m",
    "relative_humidity_2m",
    "apparent_temperature",
    "is_day",
    "precipitation",
    "rain",
    "weather_code",
    "cloud_cover",
    "wind_speed_10m",
    "wind_direction_10m",
    "wind_gusts_10m",
]

FIELD_MAP = {
    "temperature_2m": "weather_temperature_2m_c",
    "apparent_temperature": "weather_apparent_temperature_c",
    "relative_humidity_2m": "weather_relative_humidity_percent",
    "cloud_cover": "weather_cloud_cover_percent",
    "precipitation": "weather_precipitation_mm",
    "rain": "weather_rain_mm",
    "wind_speed_10m": "weather_wind_speed_10m_kph",
    "wind_direction_10m": "weather_wind_direction_10m_deg",
    "wind_gusts_10m": "weather_wind_gusts_10m_kph",
    "weather_code": "weather_code",
    "is_day": "weather_is_day",
}


@dataclass(frozen=True)
class WeatherSnapshot:
    """Normalized current weather state ready for telemetry enrichment."""

    available: bool
    observed_at: Optional[datetime] = None
    interval_seconds: Optional[int] = None
    values: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    def age_seconds(self, at: datetime) -> Optional[int]:
        if self.observed_at is None:
            return None
        return max(0, int((at - self.observed_at).total_seconds()))

    def to_measurements(self, at: datetime, max_stale_seconds: int) -> Dict[str, Any]:
        stale_seconds = self.age_seconds(at)
        base: Dict[str, Any] = {
            "weather_available": 1 if self.available else 0,
        }
        if self.observed_at is not None:
            base["weather_observed_at_epoch"] = int(self.observed_at.timestamp())
        if self.interval_seconds is not None:
            base["weather_interval_seconds"] = self.interval_seconds
        if stale_seconds is not None:
            base["weather_stale_seconds"] = stale_seconds

        if not self.available or stale_seconds is None or stale_seconds > max_stale_seconds:
            base["weather_available"] = 0
            return base

        for key, value in (self.values or {}).items():
            if value is not None:
                base[key] = value
        return base


class OpenMeteoCurrentWeatherClient:
    """Fetches and normalizes current ambient weather from Open-Meteo."""

    endpoint = "https://api.open-meteo.com/v1/forecast"

    def __init__(self, latitude: float, longitude: float, timezone_name: str, timeout_seconds: float = 10):
        self.latitude = latitude
        self.longitude = longitude
        self.timezone_name = timezone_name
        self.timeout_seconds = timeout_seconds

    def fetch_current(self) -> WeatherSnapshot:
        params = {
            "latitude": self.latitude,
            "longitude": self.longitude,
            "timezone": self.timezone_name,
            "current": ",".join(OPEN_METEO_CURRENT_FIELDS),
        }
        try:
            response = requests.get(self.endpoint, params=params, timeout=self.timeout_seconds)
            response.raise_for_status()
            return self.from_response(response.json())
        except Exception as exc:
            logger.warning("Current weather fetch failed: %s", exc)
            return WeatherSnapshot(available=False, error=str(exc))

    @staticmethod
    def from_response(payload: Dict[str, Any]) -> WeatherSnapshot:
        current = payload.get("current")
        if not isinstance(current, dict):
            return WeatherSnapshot(available=False, error="missing current weather payload")

        observed_at = parse_open_meteo_time(current.get("time"))
        if observed_at is None:
            return WeatherSnapshot(available=False, error="missing current weather time")

        values: Dict[str, Any] = {}
        for source_key, telemetry_key in FIELD_MAP.items():
            values[telemetry_key] = current.get(source_key)

        interval = current.get("interval")
        interval_seconds = int(interval) if isinstance(interval, (int, float)) and interval > 0 else None

        return WeatherSnapshot(
            available=True,
            observed_at=observed_at,
            interval_seconds=interval_seconds,
            values=values,
        )


def parse_open_meteo_time(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None

    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class CachedWeatherProvider:
    """Caches current weather so telemetry rows do not poll the provider every minute."""

    def __init__(
        self,
        client: OpenMeteoCurrentWeatherClient,
        refresh_interval_seconds: int,
        max_stale_seconds: int,
    ):
        self.client = client
        self.refresh_interval_seconds = refresh_interval_seconds
        self.max_stale_seconds = max_stale_seconds
        self._snapshot: Optional[WeatherSnapshot] = None
        self._next_refresh_at: Optional[datetime] = None

    async def get_measurements(self, at: datetime) -> Dict[str, Any]:
        if self._should_refresh(at):
            snapshot = await asyncio.to_thread(self.client.fetch_current)
            if snapshot.available:
                self._snapshot = snapshot
                interval = max(
                    self.refresh_interval_seconds,
                    snapshot.interval_seconds or 0,
                )
                self._next_refresh_at = at + timedelta(seconds=interval)
            else:
                if self._snapshot is None:
                    self._snapshot = snapshot
                retry_seconds = min(self.refresh_interval_seconds, 60)
                self._next_refresh_at = at + timedelta(seconds=retry_seconds)

        if self._snapshot is None:
            return {"weather_available": 0}
        return self._snapshot.to_measurements(at, self.max_stale_seconds)

    def _should_refresh(self, at: datetime) -> bool:
        return self._next_refresh_at is None or at >= self._next_refresh_at


def build_weather_provider(weather_config: WeatherConfig) -> Optional[CachedWeatherProvider]:
    if not weather_config.enabled:
        return None

    if weather_config.provider != "open_meteo":
        logger.warning("Unsupported weather provider configured: %s", weather_config.provider)
        return None

    if weather_config.latitude is None or weather_config.longitude is None:
        logger.warning("Weather enrichment enabled without SITE_LATITUDE/SITE_LONGITUDE")
        return None

    client = OpenMeteoCurrentWeatherClient(
        latitude=weather_config.latitude,
        longitude=weather_config.longitude,
        timezone_name=weather_config.timezone,
        timeout_seconds=weather_config.request_timeout_seconds,
    )
    return CachedWeatherProvider(
        client=client,
        refresh_interval_seconds=weather_config.refresh_interval_seconds,
        max_stale_seconds=weather_config.max_stale_seconds,
    )
