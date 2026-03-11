from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from .models import Departure, Stop, Vehicle
from .utils import parse_bool


class BusradarError(RuntimeError):
    pass


class BusradarClient:
    def __init__(
        self,
        base_url: str = "https://rest.busradar.conterra.de/prod",
        timeout_seconds: int = 15,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def fetch_stops(self) -> list[Stop]:
        payload = self._get_json("haltestellen")
        features = payload.get("features", [])
        return sorted(
            (self._parse_stop(feature) for feature in features),
            key=lambda stop: (stop.name.casefold(), stop.direction.casefold(), stop.code.casefold()),
        )

    def fetch_departures(
        self,
        stop_id: str,
        *,
        lookahead_seconds: int = 5400,
        max_departures: int = 20,
    ) -> list[Departure]:
        query = urlencode(
            {
                "sekunden": lookahead_seconds,
                "maxAnzahl": max_departures,
            }
        )
        payload = self._get_json(f"haltestellen/{quote(stop_id)}/abfahrten?{query}")
        return sorted(
            (self._parse_departure(item) for item in payload),
            key=lambda departure: departure.actual_departure,
        )

    def fetch_vehicles(self) -> list[Vehicle]:
        payload = self._get_json("fahrzeuge")
        features = payload.get("features", [])
        return sorted(
            (self._parse_vehicle(feature) for feature in features),
            key=lambda vehicle: (vehicle.line, vehicle.sequence, vehicle.vehicle_id),
        )

    def _get_json(self, path: str) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        request = Request(url, headers={"User-Agent": "MuensterBusBoard/0.1"})
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                payload = response.read().decode(charset)
        except HTTPError as exc:
            raise BusradarError(f"API request failed with HTTP {exc.code} for {url}") from exc
        except URLError as exc:
            raise BusradarError(f"API request failed for {url}: {exc.reason}") from exc

        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise BusradarError(f"API returned invalid JSON for {url}") from exc

    @staticmethod
    def _parse_stop(feature: dict[str, Any]) -> Stop:
        properties = feature.get("properties", {})
        geometry = feature.get("geometry", {})
        coordinates = geometry.get("coordinates", [0.0, 0.0])
        return Stop(
            stop_id=str(properties.get("nr", "")),
            name=str(properties.get("lbez", "")).strip(),
            code=str(properties.get("kbez", "")).strip(),
            direction=str(properties.get("richtung", "")).strip(),
            global_id=str(properties.get("global_id", "")).strip(),
            latitude=float(coordinates[1]),
            longitude=float(coordinates[0]),
        )

    @staticmethod
    def _parse_departure(item: dict[str, Any]) -> Departure:
        scheduled = int(item.get("abfahrtszeit", 0))
        actual = int(item.get("tatsaechliche_abfahrtszeit", scheduled))
        return Departure(
            stop_id=str(item.get("haltid", "")).strip(),
            line=str(item.get("linientext", "")).strip().upper(),
            destination=str(item.get("richtungstext", "")).strip(),
            scheduled_departure=scheduled,
            actual_departure=actual,
            delay_seconds=int(item.get("delay", actual - scheduled)),
            vehicle_id=_normalize_optional_string(item.get("fahrzeugid")),
            occupancy=str(item.get("besetztgrad", "Unbekannt")).strip(),
            trip_id=str(item.get("fahrtbezeichner", "")).strip(),
            predicted=parse_bool(item.get("prognosemoeglich")),
            sequence=int(item.get("sequenz", 0)),
            boarding_allowed=not parse_bool(item.get("einsteigeverbot")),
        )

    @staticmethod
    def _parse_vehicle(feature: dict[str, Any]) -> Vehicle:
        properties = feature.get("properties", {})
        geometry = feature.get("geometry", {})
        coordinates = geometry.get("coordinates", [0.0, 0.0])
        return Vehicle(
            line=str(properties.get("linientext", "")).strip().upper(),
            route_id=str(properties.get("linienid", "")).strip(),
            vehicle_id=str(properties.get("fahrzeugid", "")).strip(),
            direction=str(properties.get("richtungstext", "")).strip(),
            delay_seconds=int(properties.get("delay", 0)),
            current_stop_id=str(properties.get("akthst", "")).strip(),
            next_stop_id=str(properties.get("nachhst", "")).strip(),
            start_stop_id=str(properties.get("starthst", "")).strip(),
            target_stop_id=str(properties.get("zielhst", "")).strip(),
            trip_id=str(properties.get("fahrtbezeichner", "")).strip(),
            status=str(properties.get("fahrtstatus", "")).strip(),
            sequence=int(properties.get("sequenz", 0)),
            latitude=float(coordinates[1]),
            longitude=float(coordinates[0]),
            service_date=str(properties.get("betriebstag", "")).strip(),
            scheduled_start=int(properties.get("abfahrtstart", 0)),
            target_arrival=int(properties.get("ankunftziel", 0)),
            last_observed=int(properties.get("visfahrplanlagezst", 0)),
        )


def _normalize_optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
