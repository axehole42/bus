from __future__ import annotations

import base64
import math
import queue
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import ttk
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .models import Stop, Vehicle
from .utils import format_delay


TILE_SIZE = 256
MIN_ZOOM = 13
MAX_ZOOM = 19
MAX_LATITUDE = 85.05112878
PAN_REDRAW_DELAY_MS = 120
DEFAULT_REDRAW_DELAY_MS = 40
HOVER_RADIUS = 18.0


@dataclass(slots=True)
class MarkerHitbox:
    x: float
    y: float
    radius: float
    tooltip_text: str


def clamp_latitude(latitude: float) -> float:
    return max(-MAX_LATITUDE, min(MAX_LATITUDE, latitude))


def latlon_to_world_pixels(latitude: float, longitude: float, zoom: int) -> tuple[float, float]:
    latitude = clamp_latitude(latitude)
    scale = TILE_SIZE * (2**zoom)
    x = (longitude + 180.0) / 360.0 * scale
    lat_rad = math.radians(latitude)
    y = (1.0 - math.log(math.tan(lat_rad) + (1.0 / math.cos(lat_rad))) / math.pi) / 2.0 * scale
    return x, y


def world_pixels_to_latlon(world_x: float, world_y: float, zoom: int) -> tuple[float, float]:
    scale = TILE_SIZE * (2**zoom)
    world_x = world_x % scale
    world_y = max(0.0, min(scale, world_y))
    longitude = world_x / scale * 360.0 - 180.0
    latitude = math.degrees(math.atan(math.sinh(math.pi - (2.0 * math.pi * world_y / scale))))
    return clamp_latitude(latitude), longitude


class TileProvider:
    def __init__(self, master: tk.Misc, on_tile_ready: Callable[[], None]) -> None:
        self.master = master
        self.on_tile_ready = on_tile_ready
        self.cache_dir = Path(__file__).resolve().parents[2] / "tile_cache"
        self.cache_dir.mkdir(exist_ok=True)
        self.photos: dict[tuple[int, int, int], tk.PhotoImage] = {}
        self.loading: set[tuple[int, int, int]] = set()
        self.retry_at: dict[tuple[int, int, int], float] = {}
        self.pending: queue.Queue[tuple[tuple[int, int, int], bytes | None]] = queue.Queue()
        self.master.after(120, self._process_pending)

    def get_tile(self, zoom: int, x: int, y: int) -> tk.PhotoImage | None:
        key = (zoom, x, y)
        if key in self.photos:
            return self.photos[key]

        path = self._tile_path(*key)
        if path.exists():
            try:
                image = self._photo_from_bytes(path.read_bytes())
            except (OSError, tk.TclError):
                path.unlink(missing_ok=True)
            else:
                self.photos[key] = image
                return image

        if key in self.loading or time.monotonic() < self.retry_at.get(key, 0.0):
            return None

        self.loading.add(key)
        threading.Thread(target=self._download_tile, args=(key,), daemon=True).start()
        return None

    def _download_tile(self, key: tuple[int, int, int]) -> None:
        zoom, x, y = key
        url = f"https://tile.openstreetmap.org/{zoom}/{x}/{y}.png"
        request = Request(
            url,
            headers={
                "User-Agent": "MuensterBusBoard/0.3 (+desktop Tkinter app)",
            },
        )
        try:
            with urlopen(request, timeout=12) as response:
                payload = response.read()
            path = self._tile_path(zoom, x, y)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            self.pending.put((key, payload))
        except (HTTPError, URLError, OSError):
            self.pending.put((key, None))

    def _process_pending(self) -> None:
        changed = False
        try:
            while True:
                key, payload = self.pending.get_nowait()
                self.loading.discard(key)
                if payload is None:
                    self.retry_at[key] = time.monotonic() + 30.0
                    continue
                try:
                    self.photos[key] = self._photo_from_bytes(payload)
                except tk.TclError:
                    self.retry_at[key] = time.monotonic() + 30.0
                else:
                    changed = True
        except queue.Empty:
            pass

        if changed:
            self.on_tile_ready()

        self.master.after(120, self._process_pending)

    def _tile_path(self, zoom: int, x: int, y: int) -> Path:
        return self.cache_dir / "osm" / str(zoom) / str(x) / f"{y}.png"

    @staticmethod
    def _photo_from_bytes(payload: bytes) -> tk.PhotoImage:
        return tk.PhotoImage(data=base64.b64encode(payload).decode("ascii"))


class MapView(ttk.Frame):
    def __init__(
        self,
        master: tk.Misc,
        line_colors: dict[str, str],
        stop_name_lookup: Callable[[str], str],
    ) -> None:
        super().__init__(master)
        self.line_colors = line_colors
        self.stop_name_lookup = stop_name_lookup
        self.selected_stop: Stop | None = None
        self.vehicles: list[Vehicle] = []
        self.center_latitude = 51.96236
        self.center_longitude = 7.62571
        self.zoom = 16
        self._drag_origin: tuple[int, int, float, float] | None = None
        self._drag_last_pointer: tuple[int, int] | None = None
        self._drag_refresh_after_id: str | None = None
        self._redraw_after_id: str | None = None
        self._rendered_tiles: list[tk.PhotoImage] = []
        self._hitboxes: list[MarkerHitbox] = []
        self._is_dragging = False
        self._pending_tile_redraw = False

        self.tile_provider = TileProvider(self, self._on_tiles_ready)

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(self, style="MapToolbar.TFrame")
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        toolbar.columnconfigure(0, weight=1)

        self.title_var = tk.StringVar(value="Street Map")
        self.meta_var = tk.StringVar(value="Drag to pan, hover markers for live details")
        ttk.Label(toolbar, textvariable=self.title_var, style="MapTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(toolbar, textvariable=self.meta_var, style="MapMeta.TLabel").grid(row=1, column=0, sticky="w", pady=(2, 0))

        actions = ttk.Frame(toolbar, style="MapToolbar.TFrame")
        actions.grid(row=0, column=1, rowspan=2, sticky="e")
        ttk.Button(actions, text="-", width=3, style="Map.TButton", command=self.zoom_out).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(actions, text="+", width=3, style="Map.TButton", command=self.zoom_in).grid(row=0, column=1, padx=(0, 6))
        ttk.Button(actions, text="Center", style="Map.TButton", command=self.center_on_stop).grid(row=0, column=2)

        self.canvas = tk.Canvas(
            self,
            bg="#DDE8F7",
            highlightthickness=1,
            highlightbackground="#D6E0EF",
            relief="flat",
            cursor="hand2",
        )
        self.canvas.grid(row=1, column=0, sticky="nsew")
        self.canvas.bind("<Configure>", self._on_configure)
        self.canvas.bind("<ButtonPress-1>", self._on_drag_start)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_drag_end)
        self.canvas.bind("<Motion>", self._on_pointer_move)
        self.canvas.bind("<Leave>", self._on_pointer_leave)
        self.canvas.bind("<Double-Button-1>", self._on_double_click)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Button-4>", self._on_linux_scroll_up)
        self.canvas.bind("<Button-5>", self._on_linux_scroll_down)

    def set_state(self, stop: Stop | None, vehicles: list[Vehicle]) -> None:
        stop_changed = stop is not None and (self.selected_stop is None or self.selected_stop.stop_id != stop.stop_id)
        self.selected_stop = stop
        self.vehicles = vehicles
        if stop_changed:
            self.center_latitude = stop.latitude
            self.center_longitude = stop.longitude
        self.title_var.set(stop.label if stop else "Street Map")
        self.meta_var.set(f"{len(vehicles)} live vehicles on the current map" if stop else "Choose a stop to load the map")
        self.schedule_redraw(delay_ms=0)

    def center_on_stop(self) -> None:
        if not self.selected_stop:
            return
        self.center_latitude = self.selected_stop.latitude
        self.center_longitude = self.selected_stop.longitude
        self.schedule_redraw(delay_ms=0)

    def zoom_in(self) -> None:
        self._set_zoom(self.zoom + 1)

    def zoom_out(self) -> None:
        self._set_zoom(self.zoom - 1)

    def schedule_redraw(self, delay_ms: int = DEFAULT_REDRAW_DELAY_MS) -> None:
        if self._redraw_after_id:
            self.after_cancel(self._redraw_after_id)
        self._redraw_after_id = self.after(delay_ms, self._draw_map)

    def _on_tiles_ready(self) -> None:
        if self._is_dragging:
            self._pending_tile_redraw = True
            return
        self.schedule_redraw()

    def _draw_map(self) -> None:
        self._redraw_after_id = None
        width = max(self.canvas.winfo_width(), 420)
        height = max(self.canvas.winfo_height(), 320)
        self.canvas.delete("all")
        self._rendered_tiles.clear()
        self._hitboxes.clear()

        if not self.selected_stop:
            self.canvas.create_text(
                width / 2,
                height / 2,
                text="Choose a stop to load the map.",
                fill="#475569",
                font=("Segoe UI Semibold", 12),
            )
            return

        center_world_x, center_world_y = latlon_to_world_pixels(self.center_latitude, self.center_longitude, self.zoom)
        top_left_x = center_world_x - width / 2
        top_left_y = center_world_y - height / 2
        tile_count = 2**self.zoom

        first_tile_x = math.floor(top_left_x / TILE_SIZE)
        last_tile_x = math.floor((top_left_x + width) / TILE_SIZE)
        first_tile_y = math.floor(top_left_y / TILE_SIZE)
        last_tile_y = math.floor((top_left_y + height) / TILE_SIZE)

        for tile_x_raw in range(first_tile_x, last_tile_x + 1):
            tile_x = tile_x_raw % tile_count
            screen_x = tile_x_raw * TILE_SIZE - top_left_x
            for tile_y in range(first_tile_y, last_tile_y + 1):
                if tile_y < 0 or tile_y >= tile_count:
                    continue
                screen_y = tile_y * TILE_SIZE - top_left_y
                image = self.tile_provider.get_tile(self.zoom, tile_x, tile_y)
                if image is None:
                    self._draw_tile_placeholder(screen_x, screen_y)
                    continue
                self._rendered_tiles.append(image)
                self.canvas.create_image(screen_x, screen_y, anchor="nw", image=image, tags=("map", "tile"))

        self._draw_stop_marker(top_left_x, top_left_y)
        self._draw_vehicle_markers(top_left_x, top_left_y, width, height)
        self._draw_overlay(width, height)

    def _draw_tile_placeholder(self, screen_x: float, screen_y: float) -> None:
        self.canvas.create_rectangle(
            screen_x,
            screen_y,
            screen_x + TILE_SIZE,
            screen_y + TILE_SIZE,
            fill="#E8EEF8",
            outline="#D4DDED",
            tags=("map", "tile"),
        )
        self.canvas.create_line(screen_x, screen_y, screen_x + TILE_SIZE, screen_y + TILE_SIZE, fill="#D4DDED", tags=("map", "tile"))
        self.canvas.create_line(screen_x + TILE_SIZE, screen_y, screen_x, screen_y + TILE_SIZE, fill="#D4DDED", tags=("map", "tile"))

    def _draw_stop_marker(self, top_left_x: float, top_left_y: float) -> None:
        if not self.selected_stop:
            return
        x, y = self._screen_position(self.selected_stop.latitude, self.selected_stop.longitude, top_left_x, top_left_y)
        self.canvas.create_oval(x - 13, y - 13, x + 13, y + 13, fill="#0F172A", outline="#FFFFFF", width=3, tags=("map", "marker"))
        self.canvas.create_oval(x - 4, y - 4, x + 4, y + 4, fill="#FFFFFF", outline="", tags=("map", "marker"))
        self.canvas.create_text(
            x,
            y - 24,
            text=self.selected_stop.code or self.selected_stop.name,
            fill="#0F172A",
            font=("Segoe UI Semibold", 9),
            tags=("map", "marker"),
        )
        self._hitboxes.append(
            MarkerHitbox(
                x=x,
                y=y,
                radius=16.0,
                tooltip_text="\n".join(
                    part
                    for part in (
                        self.selected_stop.name,
                        self.selected_stop.code,
                        self.selected_stop.direction or None,
                    )
                    if part
                ),
            )
        )

    def _draw_vehicle_markers(self, top_left_x: float, top_left_y: float, width: float, height: float) -> None:
        for vehicle in sorted(self.vehicles, key=lambda item: (item.line, item.vehicle_id)):
            x, y = self._screen_position(vehicle.latitude, vehicle.longitude, top_left_x, top_left_y)
            if x < -32 or y < -32 or x > width + 32 or y > height + 32:
                continue
            color = self.line_colors.get(vehicle.line, "#2563EB")
            self.canvas.create_oval(x - 14, y - 14, x + 14, y + 14, fill="#FFFFFF", outline=color, width=3, tags=("map", "marker"))
            self.canvas.create_oval(x - 10, y - 10, x + 10, y + 10, fill=color, outline="", tags=("map", "marker"))
            self.canvas.create_text(
                x,
                y,
                text=vehicle.line,
                fill="#FFFFFF",
                font=("Segoe UI Semibold", 8),
                tags=("map", "marker"),
            )
            self._hitboxes.append(
                MarkerHitbox(
                    x=x,
                    y=y,
                    radius=HOVER_RADIUS,
                    tooltip_text=self._vehicle_tooltip(vehicle),
                )
            )

    def _draw_overlay(self, width: int, height: int) -> None:
        self.canvas.create_rectangle(14, 14, 278, 62, fill="#FFFFFF", outline="#D6E0EF", tags=("overlay",))
        self.canvas.create_text(
            28,
            28,
            anchor="w",
            text="Drag to pan. Hover points for details.",
            fill="#0F172A",
            font=("Segoe UI Semibold", 9),
            tags=("overlay",),
        )
        self.canvas.create_text(
            28,
            46,
            anchor="w",
            text=f"Zoom {self.zoom}",
            fill="#64748B",
            font=("Segoe UI", 9),
            tags=("overlay",),
        )

        self.canvas.create_rectangle(width - 228, height - 34, width - 14, height - 14, fill="#FFFFFF", outline="#D6E0EF", tags=("overlay",))
        self.canvas.create_text(
            width - 121,
            height - 24,
            text="(c) OpenStreetMap contributors",
            fill="#475569",
            font=("Segoe UI", 8),
            tags=("overlay",),
        )

    def _vehicle_tooltip(self, vehicle: Vehicle) -> str:
        current_stop = self.stop_name_lookup(vehicle.current_stop_id)
        next_stop = self.stop_name_lookup(vehicle.next_stop_id)
        lines = [
            f"Line {vehicle.line} | Bus {vehicle.vehicle_id}",
            vehicle.direction,
            f"Delay: {format_delay(vehicle.delay_seconds)}",
        ]
        if current_stop:
            lines.append(f"Current: {current_stop}")
        if next_stop:
            lines.append(f"Next: {next_stop}")
        return "\n".join(line for line in lines if line)

    def _show_tooltip(self, screen_x: float, screen_y: float, text: str) -> None:
        self.canvas.delete("tooltip")
        text_id = self.canvas.create_text(
            screen_x + 16,
            screen_y + 16,
            anchor="nw",
            justify="left",
            text=text,
            fill="#E2E8F0",
            font=("Segoe UI", 9),
            tags=("tooltip",),
        )
        left, top, right, bottom = self.canvas.bbox(text_id)
        self.canvas.create_rectangle(
            left - 10,
            top - 8,
            right + 10,
            bottom + 8,
            fill="#0F172A",
            outline="#334155",
            width=1,
            tags=("tooltip",),
        )
        self.canvas.tag_raise(text_id)

    def _hide_tooltip(self) -> None:
        self.canvas.delete("tooltip")

    def _marker_at(self, screen_x: float, screen_y: float) -> MarkerHitbox | None:
        for marker in reversed(self._hitboxes):
            dx = marker.x - screen_x
            dy = marker.y - screen_y
            if dx * dx + dy * dy <= marker.radius * marker.radius:
                return marker
        return None

    def _screen_position(self, latitude: float, longitude: float, top_left_x: float, top_left_y: float) -> tuple[float, float]:
        world_x, world_y = latlon_to_world_pixels(latitude, longitude, self.zoom)
        return world_x - top_left_x, world_y - top_left_y

    def _set_zoom(self, zoom: int, anchor_x: float | None = None, anchor_y: float | None = None) -> None:
        new_zoom = max(MIN_ZOOM, min(MAX_ZOOM, zoom))
        if new_zoom == self.zoom:
            return

        if anchor_x is None or anchor_y is None:
            anchor_x = self.canvas.winfo_width() / 2
            anchor_y = self.canvas.winfo_height() / 2

        anchor_lat, anchor_lon = self._latlon_at_screen(anchor_x, anchor_y)
        self.zoom = new_zoom
        anchor_world_x, anchor_world_y = latlon_to_world_pixels(anchor_lat, anchor_lon, self.zoom)
        center_world_x = anchor_world_x - (anchor_x - self.canvas.winfo_width() / 2)
        center_world_y = anchor_world_y - (anchor_y - self.canvas.winfo_height() / 2)
        self.center_latitude, self.center_longitude = world_pixels_to_latlon(center_world_x, center_world_y, self.zoom)
        self.schedule_redraw(delay_ms=0)

    def _latlon_at_screen(self, screen_x: float, screen_y: float) -> tuple[float, float]:
        center_world_x, center_world_y = latlon_to_world_pixels(self.center_latitude, self.center_longitude, self.zoom)
        world_x = center_world_x - self.canvas.winfo_width() / 2 + screen_x
        world_y = center_world_y - self.canvas.winfo_height() / 2 + screen_y
        return world_pixels_to_latlon(world_x, world_y, self.zoom)

    def _on_configure(self, _: tk.Event[tk.Canvas]) -> None:
        self.schedule_redraw()

    def _on_drag_start(self, event: tk.Event[tk.Canvas]) -> None:
        center_world_x, center_world_y = latlon_to_world_pixels(self.center_latitude, self.center_longitude, self.zoom)
        self._drag_origin = (event.x, event.y, center_world_x, center_world_y)
        self._drag_last_pointer = (event.x, event.y)
        self._is_dragging = True
        self._hide_tooltip()

    def _on_drag(self, event: tk.Event[tk.Canvas]) -> None:
        if self._drag_origin is None:
            return

        if self._drag_last_pointer is not None:
            delta_x = event.x - self._drag_last_pointer[0]
            delta_y = event.y - self._drag_last_pointer[1]
            self.canvas.move("map", delta_x, delta_y)

        origin_x, origin_y, center_world_x, center_world_y = self._drag_origin
        self.center_latitude, self.center_longitude = world_pixels_to_latlon(
            center_world_x - (event.x - origin_x),
            center_world_y - (event.y - origin_y),
            self.zoom,
        )
        self._drag_last_pointer = (event.x, event.y)
        if self._drag_refresh_after_id is None:
            self._drag_refresh_after_id = self.after(PAN_REDRAW_DELAY_MS, self._drag_refresh)

    def _drag_refresh(self) -> None:
        self._drag_refresh_after_id = None
        if self._is_dragging:
            self.schedule_redraw(delay_ms=0)

    def _on_drag_end(self, _: tk.Event[tk.Canvas]) -> None:
        self._drag_origin = None
        self._drag_last_pointer = None
        self._is_dragging = False
        if self._drag_refresh_after_id:
            self.after_cancel(self._drag_refresh_after_id)
            self._drag_refresh_after_id = None
        if self._pending_tile_redraw:
            self._pending_tile_redraw = False
        self.schedule_redraw(delay_ms=0)

    def _on_pointer_move(self, event: tk.Event[tk.Canvas]) -> None:
        if self._is_dragging:
            return
        marker = self._marker_at(event.x, event.y)
        if marker is None:
            self._hide_tooltip()
            return
        self._show_tooltip(event.x, event.y, marker.tooltip_text)

    def _on_pointer_leave(self, _: tk.Event[tk.Canvas]) -> None:
        self._hide_tooltip()

    def _on_double_click(self, event: tk.Event[tk.Canvas]) -> None:
        self._set_zoom(self.zoom + 1, event.x, event.y)

    def _on_mousewheel(self, event: tk.Event[tk.Canvas]) -> None:
        direction = 1 if event.delta > 0 else -1
        self._set_zoom(self.zoom + direction, event.x, event.y)

    def _on_linux_scroll_up(self, event: tk.Event[tk.Canvas]) -> None:
        self._set_zoom(self.zoom + 1, event.x, event.y)

    def _on_linux_scroll_down(self, event: tk.Event[tk.Canvas]) -> None:
        self._set_zoom(self.zoom - 1, event.x, event.y)
