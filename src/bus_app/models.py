from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Stop:
    stop_id: str
    name: str
    code: str
    direction: str
    global_id: str
    latitude: float
    longitude: float

    @property
    def label(self) -> str:
        direction = f" [{self.direction}]" if self.direction else ""
        code = f" | {self.code}" if self.code else ""
        return f"{self.name}{code}{direction}"


@dataclass(slots=True)
class Departure:
    stop_id: str
    line: str
    destination: str
    scheduled_departure: int
    actual_departure: int
    delay_seconds: int
    vehicle_id: str | None
    occupancy: str
    trip_id: str
    predicted: bool
    sequence: int
    boarding_allowed: bool


@dataclass(slots=True)
class Vehicle:
    line: str
    route_id: str
    vehicle_id: str
    direction: str
    delay_seconds: int
    current_stop_id: str
    next_stop_id: str
    start_stop_id: str
    target_stop_id: str
    trip_id: str
    status: str
    sequence: int
    latitude: float
    longitude: float
    service_date: str
    scheduled_start: int
    target_arrival: int
    last_observed: int
