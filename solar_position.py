import math
from datetime import datetime, timezone
from typing import Any, Dict


def solar_position_measurements(timestamp: datetime, latitude: float, longitude: float) -> Dict[str, Any]:
    """Calculate approximate solar geometry for production/weather analytics."""
    when = timestamp.astimezone(timezone.utc)
    day_of_year = int(when.strftime("%j"))
    fractional_hour = when.hour + when.minute / 60 + when.second / 3600

    gamma = (2 * math.pi / 365) * (day_of_year - 1 + (fractional_hour - 12) / 24)
    declination = (
        0.006918
        - 0.399912 * math.cos(gamma)
        + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2 * gamma)
        + 0.000907 * math.sin(2 * gamma)
        - 0.002697 * math.cos(3 * gamma)
        + 0.00148 * math.sin(3 * gamma)
    )
    equation_of_time = 229.18 * (
        0.000075
        + 0.001868 * math.cos(gamma)
        - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2 * gamma)
        - 0.040849 * math.sin(2 * gamma)
    )

    true_solar_time_minutes = (fractional_hour * 60 + equation_of_time + 4 * longitude) % 1440
    hour_angle_deg = true_solar_time_minutes / 4 - 180
    if hour_angle_deg < -180:
        hour_angle_deg += 360

    latitude_rad = math.radians(latitude)
    hour_angle = math.radians(hour_angle_deg)
    cos_zenith = (
        math.sin(latitude_rad) * math.sin(declination)
        + math.cos(latitude_rad) * math.cos(declination) * math.cos(hour_angle)
    )
    cos_zenith = max(-1, min(1, cos_zenith))
    zenith = math.degrees(math.acos(cos_zenith))
    elevation = 90 - zenith

    azimuth_rad = math.atan2(
        math.sin(hour_angle),
        math.cos(hour_angle) * math.sin(latitude_rad) - math.tan(declination) * math.cos(latitude_rad),
    )
    azimuth = (math.degrees(azimuth_rad) + 180) % 360

    return {
        "solar_azimuth_deg": round(azimuth, 3),
        "solar_elevation_deg": round(elevation, 3),
        "solar_zenith_deg": round(zenith, 3),
        "solar_cos_zenith": round(max(0, cos_zenith), 6),
        "solar_daylight_flag": 1 if elevation > 0 else 0,
    }
