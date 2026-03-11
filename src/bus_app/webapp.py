from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from dataclasses import asdict
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .api import BusradarClient, BusradarError
from .desktop import run_desktop_shell
from .models import Departure, Stop, Vehicle
from .settings import AppSettings, load_settings, save_settings
from .utils import haversine_meters, parse_lines


STATIC_DIR = Path(__file__).resolve().parent / "static"
STOP_CACHE_SECONDS = 60 * 60 * 6
DEFAULT_PORT = 8765


class BusService:
    def __init__(self) -> None:
        self.client = BusradarClient()
        self.settings = load_settings()
        self._stops: list[Stop] = []
        self._stop_index: dict[str, Stop] = {}
        self._stops_loaded_at = 0.0
        self._lock = threading.Lock()

    def get_settings_payload(self) -> dict[str, Any]:
        selected_stop = self.get_stop(self.settings.stop_id) if self.settings.stop_id else None
        return {
            "settings": {
                "stop_id": self.settings.stop_id,
                "stop_search": self.settings.stop_search,
                "monitored_lines": list(self.settings.monitored_lines),
                "refresh_seconds": self.settings.refresh_seconds,
                "lookahead_seconds": self.settings.lookahead_seconds,
                "max_departures": self.settings.max_departures,
            },
            "selected_stop": asdict(selected_stop) if selected_stop else None,
        }

    def update_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        stop_id = str(payload.get("stop_id", self.settings.stop_id)).strip()
        if stop_id and self.get_stop(stop_id) is None:
            raise BusradarError(f"Unknown stop_id '{stop_id}'")

        monitored_lines = parse_lines(str(payload.get("lines", ",".join(self.settings.monitored_lines))))
        refresh_seconds = max(5, int(payload.get("refresh_seconds", self.settings.refresh_seconds)))
        lookahead_seconds = max(600, int(payload.get("lookahead_seconds", self.settings.lookahead_seconds)))
        max_departures = max(1, int(payload.get("max_departures", self.settings.max_departures)))
        stop_search = str(payload.get("stop_search", self.settings.stop_search))

        self.settings = AppSettings(
            stop_id=stop_id,
            stop_search=stop_search,
            monitored_lines=monitored_lines,
            refresh_seconds=refresh_seconds,
            lookahead_seconds=lookahead_seconds,
            max_departures=max_departures,
        )
        save_settings(self.settings)
        return self.get_settings_payload()

    def get_stops(self, query: str = "", limit: int = 80) -> list[dict[str, Any]]:
        stops = self._ensure_stops()
        normalized = query.strip().casefold()
        if normalized:
            filtered = [
                stop
                for stop in stops
                if normalized in stop.name.casefold()
                or normalized in stop.code.casefold()
                or normalized in stop.stop_id.casefold()
            ]
        else:
            filtered = stops
        return [asdict(stop) for stop in filtered[:limit]]

    def get_stop(self, stop_id: str) -> Stop | None:
        self._ensure_stops()
        return self._stop_index.get(stop_id)

    def get_snapshot(
        self,
        stop_id: str,
        *,
        lines: tuple[str, ...],
        lookahead_seconds: int,
        max_departures: int,
    ) -> dict[str, Any]:
        selected_stop = self.get_stop(stop_id)
        if selected_stop is None:
            raise BusradarError(f"Unknown stop_id '{stop_id}'")

        departures = self.client.fetch_departures(
            stop_id,
            lookahead_seconds=lookahead_seconds,
            max_departures=max_departures,
        )
        vehicles = self.client.fetch_vehicles()

        filtered_departures = [item for item in departures if item.line in lines]
        filtered_vehicles = [item for item in vehicles if item.line in lines]

        return {
            "selected_stop": asdict(selected_stop),
            "lines": list(lines),
            "refresh_seconds": self.settings.refresh_seconds,
            "departures": [self._serialize_departure(item) for item in filtered_departures],
            "vehicles": [self._serialize_vehicle(item, selected_stop) for item in filtered_vehicles],
            "server_time": int(time.time()),
        }

    def _serialize_departure(self, departure: Departure) -> dict[str, Any]:
        stop = self._stop_index.get(departure.stop_id)
        payload = asdict(departure)
        payload["platform_label"] = stop.code if stop and stop.code else departure.stop_id
        payload["platform_name"] = stop.name if stop else departure.stop_id
        payload["direction_label"] = stop.direction if stop else ""
        return payload

    def _serialize_vehicle(self, vehicle: Vehicle, selected_stop: Stop) -> dict[str, Any]:
        payload = asdict(vehicle)
        current_stop = self._stop_index.get(vehicle.current_stop_id)
        next_stop = self._stop_index.get(vehicle.next_stop_id)
        payload["current_stop_name"] = current_stop.name if current_stop else vehicle.current_stop_id
        payload["next_stop_name"] = next_stop.name if next_stop else vehicle.next_stop_id
        payload["distance_to_selected_m"] = haversine_meters(
            selected_stop.latitude,
            selected_stop.longitude,
            vehicle.latitude,
            vehicle.longitude,
        )
        return payload

    def _ensure_stops(self) -> list[Stop]:
        with self._lock:
            if self._stops and time.time() - self._stops_loaded_at < STOP_CACHE_SECONDS:
                return self._stops
            self._stops = self.client.fetch_stops()
            self._stop_index = {stop.stop_id: stop for stop in self._stops}
            self._stops_loaded_at = time.time()
            return self._stops


class BusRequestHandler(BaseHTTPRequestHandler):
    server_version = "MuensterBusBoard/0.5"

    def __init__(self, *args: object, service: BusService, **kwargs: object) -> None:
        self.service = service
        super().__init__(*args, **kwargs)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/config":
                self._send_json(self.service.get_settings_payload())
                return
            if parsed.path == "/api/stops":
                query = parse_qs(parsed.query).get("q", [""])[0]
                self._send_json({"items": self.service.get_stops(query)})
                return
            if parsed.path == "/api/snapshot":
                params = parse_qs(parsed.query)
                stop_id = params.get("stop_id", [""])[0].strip()
                if not stop_id:
                    self._send_json({"error": "Missing stop_id"}, status=HTTPStatus.BAD_REQUEST)
                    return
                lines = parse_lines(params.get("lines", [",".join(self.service.settings.monitored_lines)])[0])
                lookahead_seconds = max(600, int(params.get("lookahead_seconds", [str(self.service.settings.lookahead_seconds)])[0]))
                max_departures = max(1, int(params.get("max_departures", [str(self.service.settings.max_departures)])[0]))
                payload = self.service.get_snapshot(
                    stop_id,
                    lines=lines,
                    lookahead_seconds=lookahead_seconds,
                    max_departures=max_departures,
                )
                self._send_json(payload)
                return

            self._serve_static(parsed.path)
        except BusradarError as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_GATEWAY)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:  # noqa: BLE001
            self._send_json({"error": f"Unexpected server error: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            if parsed.path != "/api/settings":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            content_length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(content_length) or b"{}")
            result = self.service.update_settings(payload)
            self._send_json(result)
        except json.JSONDecodeError:
            self._send_json({"error": "Invalid JSON payload"}, status=HTTPStatus.BAD_REQUEST)
        except BusradarError as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:  # noqa: BLE001
            self._send_json({"error": f"Unexpected server error: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return

    def _serve_static(self, path: str) -> None:
        target = "index.html" if path in {"", "/"} else path.lstrip("/")
        file_path = (STATIC_DIR / target).resolve()
        if not str(file_path).startswith(str(STATIC_DIR.resolve())) or not file_path.exists():
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        content_type = _content_type(file_path.suffix)
        payload = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_json(self, payload: dict[str, Any], *, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _content_type(suffix: str) -> str:
    return {
        ".html": "text/html; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".js": "application/javascript; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".svg": "image/svg+xml",
    }.get(suffix.lower(), "application/octet-stream")


def _find_available_port(host: str, preferred_port: int) -> int:
    for port in range(preferred_port, preferred_port + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if sock.connect_ex((host, port)) != 0:
                return port
    raise RuntimeError("Could not find a free port for the local web app")


def _open_in_chromium(url: str) -> bool:
    candidates = ["msedge", "chrome", "chromium", "brave"]
    for candidate in candidates:
        executable = shutil.which(candidate)
        if executable:
            subprocess.Popen([executable, "--new-window", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
    return webbrowser.open_new(url)


def _start_server(host: str, port: int, service: BusService) -> tuple[ThreadingHTTPServer, threading.Thread]:
    handler = partial(BusRequestHandler, service=service)
    server = ThreadingHTTPServer((host, port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _shutdown_server(server: ThreadingHTTPServer, thread: threading.Thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def _wait_for_interrupt(thread: threading.Thread) -> None:
    while thread.is_alive():
        thread.join(timeout=0.5)


def main() -> None:
    host = "127.0.0.1"
    port = _find_available_port(host, DEFAULT_PORT)
    service = BusService()
    server, thread = _start_server(host, port, service)
    url = f"http://{host}:{port}"

    print(f"Muenster Bus Board running at {url}")

    try:
        if os.environ.get("BUS_APP_NO_BROWSER") == "1":
            print("Press Ctrl+C to stop the server.")
            _wait_for_interrupt(thread)
            return

        if os.environ.get("BUS_APP_FORCE_BROWSER") != "1":
            print("Launching desktop shell...")
            if run_desktop_shell(url):
                return
            print("Desktop shell unavailable. Falling back to an external Chromium browser.")
            print("Install dependencies with 'python -m pip install -e .' to enable the embedded shell.")

        print("Press Ctrl+C to stop the server.")
        _open_in_chromium(url)
        _wait_for_interrupt(thread)
    except KeyboardInterrupt:
        print("\nStopping...")
        sys.exit(0)
    finally:
        _shutdown_server(server, thread)
