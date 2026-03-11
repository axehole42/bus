from __future__ import annotations

import math
from datetime import datetime
from zoneinfo import ZoneInfo


LOCAL_TZ = ZoneInfo("Europe/Berlin")


def parse_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() == "true"


def parse_lines(value: str) -> tuple[str, ...]:
    parts = [segment.strip().upper() for segment in value.split(",")]
    lines = tuple(dict.fromkeys(part for part in parts if part))
    return lines or ("5", "11", "22")


def format_clock(timestamp: int | None) -> str:
    if not timestamp:
        return "-"
    return datetime.fromtimestamp(timestamp, LOCAL_TZ).strftime("%H:%M:%S")


def format_eta(timestamp: int | None, now: int | None = None) -> str:
    if not timestamp:
        return "-"
    reference = now or int(datetime.now(LOCAL_TZ).timestamp())
    seconds = timestamp - reference
    if seconds <= 0:
        return "due"
    minutes = math.ceil(seconds / 60)
    return f"{minutes} min"


def format_delay(seconds: int | None) -> str:
    if seconds is None:
        return "-"
    if seconds == 0:
        return "on time"
    minutes = math.ceil(abs(seconds) / 60)
    sign = "+" if seconds > 0 else "-"
    return f"{sign}{minutes} min"


def haversine_meters(
    latitude_a: float,
    longitude_a: float,
    latitude_b: float,
    longitude_b: float,
) -> float:
    radius = 6_371_000
    lat_a = math.radians(latitude_a)
    lat_b = math.radians(latitude_b)
    delta_lat = math.radians(latitude_b - latitude_a)
    delta_lon = math.radians(longitude_b - longitude_a)

    sin_lat = math.sin(delta_lat / 2)
    sin_lon = math.sin(delta_lon / 2)
    value = sin_lat**2 + math.cos(lat_a) * math.cos(lat_b) * sin_lon**2
    arc = 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))
    return radius * arc


def format_distance(distance_m: float | None) -> str:
    if distance_m is None:
        return "-"
    if distance_m < 1000:
        return f"{distance_m:.0f} m"
    return f"{distance_m / 1000:.1f} km"
