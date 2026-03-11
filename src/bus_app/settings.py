from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


DEFAULT_LINES = ("5", "11", "22")
DEFAULT_REFRESH_SECONDS = 15
DEFAULT_LOOKAHEAD_SECONDS = 5400
DEFAULT_MAX_DEPARTURES = 20
SETTINGS_PATH = Path(__file__).resolve().parents[2] / "bus_settings.json"


@dataclass(slots=True)
class AppSettings:
    stop_id: str = ""
    stop_search: str = ""
    monitored_lines: tuple[str, ...] = DEFAULT_LINES
    refresh_seconds: int = DEFAULT_REFRESH_SECONDS
    lookahead_seconds: int = DEFAULT_LOOKAHEAD_SECONDS
    max_departures: int = DEFAULT_MAX_DEPARTURES


def load_settings() -> AppSettings:
    if not SETTINGS_PATH.exists():
        return AppSettings()

    try:
        payload = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return AppSettings()

    return AppSettings(
        stop_id=str(payload.get("stop_id", "")),
        stop_search=str(payload.get("stop_search", "")),
        monitored_lines=tuple(str(line) for line in payload.get("monitored_lines", DEFAULT_LINES)),
        refresh_seconds=max(5, int(payload.get("refresh_seconds", DEFAULT_REFRESH_SECONDS))),
        lookahead_seconds=max(600, int(payload.get("lookahead_seconds", DEFAULT_LOOKAHEAD_SECONDS))),
        max_departures=max(1, int(payload.get("max_departures", DEFAULT_MAX_DEPARTURES))),
    )


def save_settings(settings: AppSettings) -> None:
    payload = asdict(settings)
    SETTINGS_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
