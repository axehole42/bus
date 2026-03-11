from __future__ import annotations

import queue
import threading
import tkinter as tk
from dataclasses import replace
from datetime import datetime
from tkinter import messagebox, ttk

from .api import BusradarClient, BusradarError
from .map_view import MapView
from .models import Departure, Stop, Vehicle
from .settings import AppSettings, load_settings, save_settings
from .utils import (
    LOCAL_TZ,
    format_clock,
    format_delay,
    format_distance,
    format_eta,
    haversine_meters,
    parse_lines,
)


PALETTE = {
    "app_bg": "#EAF2FF",
    "sidebar_bg": "#091120",
    "sidebar_panel": "#101A2C",
    "sidebar_border": "#21324D",
    "sidebar_text": "#E7EEF9",
    "sidebar_muted": "#93A4BF",
    "sidebar_input": "#15233A",
    "hero_bg": "#0F1C33",
    "hero_border": "#1C335B",
    "hero_chip": "#18345D",
    "card_bg": "#FFFFFF",
    "card_border": "#D5E1EF",
    "card_heading": "#0F172A",
    "text": "#0F172A",
    "muted": "#64748B",
    "accent": "#2563EB",
    "accent_hover": "#1D4ED8",
    "accent_soft": "#DBEAFE",
    "selection": "#DBEAFE",
}

LINE_COLORS = {
    "5": "#14B8A6",
    "11": "#F97316",
    "22": "#2563EB",
}


class BusDashboard(ttk.Frame):
    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master, padding=18)
        self.master = master
        self.client = BusradarClient()
        self.settings = load_settings()
        self.result_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.is_refreshing = False
        self.refresh_after_id: str | None = None
        self.current_snapshot: tuple[list[Departure], list[Vehicle]] = ([], [])
        self.stops: list[Stop] = []
        self.stop_index: dict[str, Stop] = {}
        self.filtered_stops: list[Stop] = []
        self.selected_stop: Stop | None = None
        self.selected_group: list[Stop] = []

        self.search_var = tk.StringVar(value=self.settings.stop_search)
        self.lines_var = tk.StringVar(value=",".join(self.settings.monitored_lines))
        self.refresh_var = tk.StringVar(value=str(self.settings.refresh_seconds))
        self.lookahead_var = tk.StringVar(value=str(self.settings.lookahead_seconds // 60))
        self.status_var = tk.StringVar(value="Loading stop list...")
        self.selected_stop_var = tk.StringVar(value="No stop selected")
        self.last_update_var = tk.StringVar(value="Last update: never")

        self._configure_style()
        self._build_layout()
        self._poll_results()
        self._load_stops()

    def _configure_style(self) -> None:
        self.master.title("Muenster Bus Board")
        self.master.geometry("1440x920")
        self.master.minsize(1220, 760)
        self.master.configure(bg=PALETTE["app_bg"])

        style = ttk.Style(self.master)
        style.theme_use("clam")
        style.configure(".", font=("Segoe UI", 10))
        style.configure("App.TFrame", background=PALETTE["app_bg"])
        style.configure("Card.TFrame", background=PALETTE["card_bg"])
        style.configure("Card.TLabelframe", background=PALETTE["card_bg"], bordercolor=PALETTE["card_border"], relief="solid")
        style.configure(
            "Card.TLabelframe.Label",
            background=PALETTE["card_bg"],
            foreground=PALETTE["card_heading"],
            font=("Segoe UI Semibold", 10),
        )
        style.configure("Card.TLabel", background=PALETTE["card_bg"], foreground=PALETTE["text"])
        style.configure("Muted.Card.TLabel", background=PALETTE["card_bg"], foreground=PALETTE["muted"])
        style.configure(
            "Treeview",
            background=PALETTE["card_bg"],
            fieldbackground=PALETTE["card_bg"],
            rowheight=30,
            bordercolor=PALETTE["card_border"],
            foreground=PALETTE["text"],
        )
        style.configure(
            "Treeview.Heading",
            background="#EFF5FD",
            foreground=PALETTE["text"],
            relief="flat",
            font=("Segoe UI Semibold", 10),
        )
        style.map("Treeview", background=[("selected", PALETTE["selection"])], foreground=[("selected", PALETTE["text"])])
        style.configure("MapToolbar.TFrame", background=PALETTE["card_bg"])
        style.configure("MapTitle.TLabel", background=PALETTE["card_bg"], foreground=PALETTE["text"], font=("Segoe UI Semibold", 11))
        style.configure("MapMeta.TLabel", background=PALETTE["card_bg"], foreground=PALETTE["muted"], font=("Segoe UI", 9))
        style.configure(
            "Map.TButton",
            padding=(10, 6),
            background=PALETTE["card_bg"],
            foreground=PALETTE["text"],
            bordercolor=PALETTE["card_border"],
        )
        style.map("Map.TButton", background=[("active", "#F6FAFF")])

    def _build_layout(self) -> None:
        self.pack(fill="both", expand=True)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        self.sidebar = tk.Frame(
            self,
            bg=PALETTE["sidebar_bg"],
            padx=20,
            pady=20,
            highlightbackground=PALETTE["sidebar_border"],
            highlightthickness=1,
            width=330,
        )
        self.sidebar.grid(row=0, column=0, sticky="nsw", padx=(0, 18))
        self.sidebar.grid_propagate(False)
        self.sidebar.rowconfigure(3, weight=1)

        tk.Label(
            self.sidebar,
            text="Transit\nCompass",
            bg=PALETTE["sidebar_bg"],
            fg=PALETTE["sidebar_text"],
            font=("Segoe UI Semibold", 24),
            justify="left",
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            self.sidebar,
            text="Live inbound board for Muenster with map tracking and delay context.",
            bg=PALETTE["sidebar_bg"],
            fg=PALETTE["sidebar_muted"],
            font=("Segoe UI", 10),
            justify="left",
            wraplength=268,
        ).grid(row=1, column=0, sticky="w", pady=(8, 18))

        self._build_sidebar_controls()

        content = ttk.Frame(self, style="App.TFrame")
        content.grid(row=0, column=1, sticky="nsew")
        content.columnconfigure(0, weight=1)
        content.rowconfigure(1, weight=1)

        hero = tk.Frame(
            content,
            bg=PALETTE["hero_bg"],
            padx=22,
            pady=18,
            highlightbackground=PALETTE["hero_border"],
            highlightthickness=1,
        )
        hero.grid(row=0, column=0, sticky="ew", pady=(0, 16))
        hero.columnconfigure(0, weight=1)
        hero.columnconfigure(1, weight=0)

        tk.Label(
            hero,
            text="Muenster Bus Board",
            bg=PALETTE["hero_bg"],
            fg="#F8FBFF",
            font=("Segoe UI Semibold", 24),
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            hero,
            text="Live departures, delays, and moving vehicles around your selected stop.",
            bg=PALETTE["hero_bg"],
            fg="#B6C6DF",
            font=("Segoe UI", 11),
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

        chips = tk.Frame(hero, bg=PALETTE["hero_bg"])
        chips.grid(row=0, column=1, rowspan=2, sticky="e")
        self.stop_chip = tk.Label(
            chips,
            textvariable=self.selected_stop_var,
            bg=PALETTE["hero_chip"],
            fg="#F8FBFF",
            font=("Segoe UI Semibold", 9),
            padx=12,
            pady=8,
        )
        self.stop_chip.grid(row=0, column=0, sticky="e")
        self.update_chip = tk.Label(
            chips,
            textvariable=self.last_update_var,
            bg=PALETTE["hero_chip"],
            fg="#CFE0F7",
            font=("Segoe UI", 9),
            padx=12,
            pady=8,
        )
        self.update_chip.grid(row=1, column=0, sticky="e", pady=(8, 0))

        body = ttk.Frame(content, style="App.TFrame")
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=3)
        body.rowconfigure(1, weight=2)

        departures_frame = ttk.LabelFrame(body, text="Departures", padding=14, style="Card.TLabelframe")
        departures_frame.grid(row=0, column=0, sticky="nsew", pady=(0, 16))
        departures_frame.columnconfigure(0, weight=1)
        departures_frame.rowconfigure(0, weight=1)
        self.departures_tree = self._build_tree(
            departures_frame,
            columns=("stop", "line", "eta", "live", "scheduled", "delay", "destination", "occupancy", "vehicle"),
            headings={
                "stop": "Platform",
                "line": "Line",
                "eta": "ETA",
                "live": "Live",
                "scheduled": "Scheduled",
                "delay": "Delay",
                "destination": "Destination",
                "occupancy": "Load",
                "vehicle": "Vehicle",
            },
            widths={
                "stop": 120,
                "line": 70,
                "eta": 84,
                "live": 92,
                "scheduled": 92,
                "delay": 92,
                "destination": 320,
                "occupancy": 150,
                "vehicle": 84,
            },
        )

        lower = ttk.Frame(body, style="App.TFrame")
        lower.grid(row=1, column=0, sticky="nsew")
        lower.columnconfigure(0, weight=3)
        lower.columnconfigure(1, weight=2)
        lower.rowconfigure(0, weight=1)

        vehicles_frame = ttk.LabelFrame(lower, text="Vehicle Feed", padding=14, style="Card.TLabelframe")
        vehicles_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
        vehicles_frame.columnconfigure(0, weight=1)
        vehicles_frame.rowconfigure(0, weight=1)
        self.vehicles_tree = self._build_tree(
            vehicles_frame,
            columns=("line", "vehicle", "distance", "delay", "current", "next", "direction", "coords"),
            headings={
                "line": "Line",
                "vehicle": "Vehicle",
                "distance": "To Stop",
                "delay": "Delay",
                "current": "Current Stop",
                "next": "Next Stop",
                "direction": "Direction",
                "coords": "Coords",
            },
            widths={
                "line": 62,
                "vehicle": 76,
                "distance": 86,
                "delay": 86,
                "current": 180,
                "next": 180,
                "direction": 220,
                "coords": 150,
            },
        )

        map_frame = ttk.LabelFrame(lower, text="Live Map", padding=14, style="Card.TLabelframe")
        map_frame.grid(row=0, column=1, sticky="nsew")
        map_frame.columnconfigure(0, weight=1)
        map_frame.rowconfigure(0, weight=1)
        self.map_view = MapView(map_frame, LINE_COLORS, self._lookup_stop_name)
        self.map_view.grid(row=0, column=0, sticky="nsew")

    def _build_sidebar_controls(self) -> None:
        panel = tk.Frame(
            self.sidebar,
            bg=PALETTE["sidebar_panel"],
            padx=14,
            pady=14,
            highlightbackground=PALETTE["sidebar_border"],
            highlightthickness=1,
        )
        panel.grid(row=2, column=0, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(3, weight=1)

        self._sidebar_label(panel, "Stop search").grid(row=0, column=0, sticky="w")
        self.search_entry = self._sidebar_entry(panel, self.search_var)
        self.search_entry.grid(row=1, column=0, sticky="ew", pady=(6, 12))
        self.search_var.trace_add("write", self._on_search_changed)

        self.stop_listbox = tk.Listbox(
            panel,
            height=12,
            exportselection=False,
            bg=PALETTE["sidebar_input"],
            fg=PALETTE["sidebar_text"],
            bd=0,
            highlightthickness=1,
            highlightbackground=PALETTE["sidebar_border"],
            highlightcolor=PALETTE["accent"],
            selectbackground=PALETTE["accent"],
            selectforeground="#FFFFFF",
            relief="flat",
            font=("Segoe UI", 10),
        )
        self.stop_listbox.grid(row=2, column=0, sticky="nsew", pady=(0, 12))
        self.stop_listbox.bind("<<ListboxSelect>>", self._on_stop_selected)

        self.selection_label = tk.Label(
            panel,
            textvariable=self.selected_stop_var,
            bg=PALETTE["sidebar_panel"],
            fg=PALETTE["sidebar_text"],
            font=("Segoe UI Semibold", 10),
            justify="left",
            wraplength=260,
        )
        self.selection_label.grid(row=3, column=0, sticky="ew", pady=(0, 12))

        self._sidebar_label(panel, "Tracked lines").grid(row=4, column=0, sticky="w")
        self.lines_entry = self._sidebar_entry(panel, self.lines_var)
        self.lines_entry.grid(row=5, column=0, sticky="ew", pady=(6, 10))

        grid = tk.Frame(panel, bg=PALETTE["sidebar_panel"])
        grid.grid(row=6, column=0, sticky="ew")
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)

        self._sidebar_label(grid, "Refresh (sec)").grid(row=0, column=0, sticky="w")
        self._sidebar_label(grid, "Horizon (min)").grid(row=0, column=1, sticky="w")
        self.refresh_entry = self._sidebar_entry(grid, self.refresh_var, width=12)
        self.refresh_entry.grid(row=1, column=0, sticky="ew", padx=(0, 8), pady=(6, 0))
        self.lookahead_entry = self._sidebar_entry(grid, self.lookahead_var, width=12)
        self.lookahead_entry.grid(row=1, column=1, sticky="ew", pady=(6, 0))

        buttons = tk.Frame(panel, bg=PALETTE["sidebar_panel"])
        buttons.grid(row=7, column=0, sticky="ew", pady=(14, 0))
        buttons.columnconfigure(0, weight=1)
        buttons.columnconfigure(1, weight=1)

        self.refresh_button = self._sidebar_button(buttons, "Refresh", self.refresh_now, primary=True)
        self.refresh_button.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.save_button = self._sidebar_button(buttons, "Save Defaults", self._save_current_settings, primary=False)
        self.save_button.grid(row=0, column=1, sticky="ew")

        self.status_label = tk.Label(
            self.sidebar,
            textvariable=self.status_var,
            bg=PALETTE["sidebar_bg"],
            fg=PALETTE["sidebar_muted"],
            font=("Segoe UI", 10),
            justify="left",
            wraplength=280,
        )
        self.status_label.grid(row=4, column=0, sticky="sw", pady=(18, 0))

    def _sidebar_label(self, master: tk.Misc, text: str) -> tk.Label:
        return tk.Label(
            master,
            text=text,
            bg=master.cget("bg"),
            fg=PALETTE["sidebar_muted"],
            font=("Segoe UI Semibold", 9),
            justify="left",
        )

    def _sidebar_entry(self, master: tk.Misc, variable: tk.StringVar, width: int = 28) -> tk.Entry:
        return tk.Entry(
            master,
            textvariable=variable,
            width=width,
            bg=PALETTE["sidebar_input"],
            fg=PALETTE["sidebar_text"],
            insertbackground=PALETTE["sidebar_text"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=PALETTE["sidebar_border"],
            highlightcolor=PALETTE["accent"],
            bd=0,
            font=("Segoe UI", 10),
        )

    def _sidebar_button(self, master: tk.Misc, text: str, command: object, *, primary: bool) -> tk.Button:
        if primary:
            return tk.Button(
                master,
                text=text,
                command=command,
                bg=PALETTE["accent"],
                fg="#FFFFFF",
                activebackground=PALETTE["accent_hover"],
                activeforeground="#FFFFFF",
                relief="flat",
                bd=0,
                padx=10,
                pady=10,
                font=("Segoe UI Semibold", 10),
                cursor="hand2",
            )
        return tk.Button(
            master,
            text=text,
            command=command,
            bg=PALETTE["sidebar_input"],
            fg=PALETTE["sidebar_text"],
            activebackground="#1B304E",
            activeforeground="#FFFFFF",
            relief="flat",
            bd=0,
            padx=10,
            pady=10,
            font=("Segoe UI Semibold", 10),
            cursor="hand2",
        )

    def _build_tree(
        self,
        parent: ttk.Frame,
        *,
        columns: tuple[str, ...],
        headings: dict[str, str],
        widths: dict[str, int],
    ) -> ttk.Treeview:
        tree = ttk.Treeview(parent, columns=columns, show="headings", selectmode="browse")
        tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        tree.configure(yscrollcommand=scrollbar.set)

        for column in columns:
            tree.heading(column, text=headings[column])
            tree.column(column, width=widths[column], anchor="w")
        return tree

    def _on_search_changed(self, *_: object) -> None:
        self._refresh_stop_list()

    def _load_stops(self) -> None:
        threading.Thread(target=self._load_stops_worker, daemon=True).start()

    def _load_stops_worker(self) -> None:
        try:
            stops = self.client.fetch_stops()
            self.result_queue.put(("stops_loaded", stops))
        except Exception as exc:  # noqa: BLE001
            self.result_queue.put(("error", exc))

    def refresh_now(self) -> None:
        if self.is_refreshing:
            return
        if not self.selected_stop:
            self.status_var.set("Select a stop first.")
            return

        try:
            refresh_seconds = max(5, int(self.refresh_var.get().strip()))
            lookahead_seconds = max(600, int(self.lookahead_var.get().strip()) * 60)
            monitored_lines = parse_lines(self.lines_var.get())
        except ValueError:
            self.status_var.set("Refresh interval and horizon must be numbers.")
            return

        self.settings = replace(
            self.settings,
            stop_id=self.selected_stop.stop_id,
            stop_search=self.search_var.get().strip(),
            monitored_lines=monitored_lines,
            refresh_seconds=refresh_seconds,
            lookahead_seconds=lookahead_seconds,
        )

        self.is_refreshing = True
        self.status_var.set("Refreshing live data...")
        stop_ids = tuple(stop.stop_id for stop in (self.selected_group or [self.selected_stop]))
        worker = threading.Thread(
            target=self._refresh_worker,
            args=(stop_ids, monitored_lines, lookahead_seconds, self.settings.max_departures),
            daemon=True,
        )
        worker.start()

    def _refresh_worker(
        self,
        stop_ids: tuple[str, ...],
        monitored_lines: tuple[str, ...],
        lookahead_seconds: int,
        max_departures: int,
    ) -> None:
        try:
            departures: list[Departure] = []
            for stop_id in stop_ids:
                departures.extend(
                    self.client.fetch_departures(
                        stop_id,
                        lookahead_seconds=lookahead_seconds,
                        max_departures=max_departures,
                    )
                )
            vehicles = self.client.fetch_vehicles()
            filtered_departures = sorted(
                (item for item in departures if item.line in monitored_lines),
                key=lambda item: item.actual_departure,
            )
            filtered_vehicles = [item for item in vehicles if item.line in monitored_lines]
            self.result_queue.put(("snapshot_loaded", (filtered_departures, filtered_vehicles)))
        except Exception as exc:  # noqa: BLE001
            self.result_queue.put(("error", exc))

    def _poll_results(self) -> None:
        try:
            while True:
                event, payload = self.result_queue.get_nowait()
                if event == "stops_loaded":
                    self._handle_stops_loaded(payload)
                elif event == "snapshot_loaded":
                    self._handle_snapshot_loaded(payload)
                elif event == "error":
                    self._handle_error(payload)
        except queue.Empty:
            pass

        self.after(250, self._poll_results)

    def _handle_stops_loaded(self, stops: object) -> None:
        self.stops = list(stops)
        self.stop_index = {stop.stop_id: stop for stop in self.stops}
        self.status_var.set(f"Loaded {len(self.stops)} stops.")
        self._refresh_stop_list()
        self._restore_selected_stop()
        if self.selected_stop:
            self.refresh_now()

    def _restore_selected_stop(self) -> None:
        if not self.settings.stop_id:
            return
        stop = self.stop_index.get(self.settings.stop_id)
        if not stop:
            return
        self._set_selected_stop(stop)
        if stop in self.filtered_stops:
            index = self.filtered_stops.index(stop)
            self.stop_listbox.selection_clear(0, tk.END)
            self.stop_listbox.selection_set(index)
            self.stop_listbox.see(index)

    def _refresh_stop_list(self) -> None:
        query = self.search_var.get().strip().casefold()
        if not query:
            self.filtered_stops = self.stops[:80]
        else:
            self.filtered_stops = [
                stop
                for stop in self.stops
                if query in stop.name.casefold() or query in stop.code.casefold() or query in stop.stop_id.casefold()
            ][:80]

        self.stop_listbox.delete(0, tk.END)
        for stop in self.filtered_stops:
            self.stop_listbox.insert(tk.END, stop.label)

    def _on_stop_selected(self, _: object) -> None:
        selection = self.stop_listbox.curselection()
        if not selection:
            return
        stop = self.filtered_stops[selection[0]]
        self._set_selected_stop(stop)
        self.refresh_now()

    def _set_selected_stop(self, stop: Stop) -> None:
        self.selected_stop = stop
        self.selected_group = self._stops_for_selection(stop)
        group_label = self._selected_group_label()
        self.selected_stop_var.set(group_label)
        self.status_var.set(f"Tracking {group_label}.")
        self.map_view.set_state(self.selected_stop, self.current_snapshot[1])

    def _handle_snapshot_loaded(self, payload: object) -> None:
        self.is_refreshing = False
        self.current_snapshot = payload  # type: ignore[assignment]
        departures, vehicles = self.current_snapshot
        self._populate_departures(departures, vehicles)
        self._populate_vehicles(vehicles)
        self.map_view.set_state(self.selected_stop, vehicles)
        timestamp = datetime.now(LOCAL_TZ).strftime("%H:%M:%S")
        self.last_update_var.set(f"Last update: {timestamp}")
        self.status_var.set(
            f"{self._selected_group_label()}: {len(departures)} departures and {len(vehicles)} vehicles for lines {', '.join(parse_lines(self.lines_var.get()))}."
        )
        save_settings(self.settings)
        self._schedule_next_refresh()

    def _populate_departures(self, departures: list[Departure], vehicles: list[Vehicle]) -> None:
        for item in self.departures_tree.get_children():
            self.departures_tree.delete(item)

        vehicle_ids = {vehicle.vehicle_id for vehicle in vehicles}
        now = int(datetime.now(LOCAL_TZ).timestamp())

        for departure in departures:
            tracked = departure.vehicle_id if departure.vehicle_id in vehicle_ids else "-"
            stop = self.stop_index.get(departure.stop_id)
            platform_label = stop.code if stop and stop.code else departure.stop_id
            self.departures_tree.insert(
                "",
                "end",
                values=(
                    platform_label,
                    departure.line,
                    format_eta(departure.actual_departure, now),
                    format_clock(departure.actual_departure),
                    format_clock(departure.scheduled_departure),
                    format_delay(departure.delay_seconds),
                    departure.destination,
                    departure.occupancy,
                    tracked,
                ),
            )

    def _populate_vehicles(self, vehicles: list[Vehicle]) -> None:
        for item in self.vehicles_tree.get_children():
            self.vehicles_tree.delete(item)

        for vehicle in sorted(vehicles, key=self._vehicle_sort_key):
            current = self.stop_index.get(vehicle.current_stop_id)
            next_stop = self.stop_index.get(vehicle.next_stop_id)
            distance = self._distance_to_selected_stop(vehicle)
            self.vehicles_tree.insert(
                "",
                "end",
                values=(
                    vehicle.line,
                    vehicle.vehicle_id,
                    format_distance(distance),
                    format_delay(vehicle.delay_seconds),
                    current.name if current else vehicle.current_stop_id,
                    next_stop.name if next_stop else vehicle.next_stop_id,
                    vehicle.direction,
                    f"{vehicle.latitude:.5f}, {vehicle.longitude:.5f}",
                ),
            )

    def _vehicle_sort_key(self, vehicle: Vehicle) -> tuple[str, float, str]:
        distance = self._distance_to_selected_stop(vehicle)
        rank = distance if distance is not None else 10_000_000.0
        return (vehicle.line, rank, vehicle.vehicle_id)

    def _distance_to_selected_stop(self, vehicle: Vehicle) -> float | None:
        group = self.selected_group or ([self.selected_stop] if self.selected_stop else [])
        if not group:
            return None
        return min(
            haversine_meters(
                stop.latitude,
                stop.longitude,
                vehicle.latitude,
                vehicle.longitude,
            )
            for stop in group
        )

    def _save_current_settings(self) -> None:
        if self.selected_stop:
            stop_id = self.selected_stop.stop_id
        else:
            stop_id = self.settings.stop_id

        try:
            settings = AppSettings(
                stop_id=stop_id,
                stop_search=self.search_var.get().strip(),
                monitored_lines=parse_lines(self.lines_var.get()),
                refresh_seconds=max(5, int(self.refresh_var.get().strip())),
                lookahead_seconds=max(600, int(self.lookahead_var.get().strip()) * 60),
                max_departures=self.settings.max_departures,
            )
        except ValueError:
            messagebox.showerror("Invalid settings", "Refresh interval and horizon must be numeric values.")
            return

        self.settings = settings
        save_settings(settings)
        self.status_var.set("Saved defaults to bus_settings.json.")

    def _handle_error(self, exc: object) -> None:
        self.is_refreshing = False
        self.status_var.set(self._format_error(exc))
        self._schedule_next_refresh()

    def _format_error(self, exc: object) -> str:
        if isinstance(exc, BusradarError):
            return str(exc)
        if isinstance(exc, Exception):
            return f"Unexpected error: {exc}"
        return "Unexpected error."

    def _schedule_next_refresh(self) -> None:
        if self.refresh_after_id:
            self.after_cancel(self.refresh_after_id)
        try:
            delay_ms = max(5, int(self.refresh_var.get() or self.settings.refresh_seconds)) * 1000
        except ValueError:
            delay_ms = self.settings.refresh_seconds * 1000
        self.refresh_after_id = self.after(delay_ms, self.refresh_now)

    def _lookup_stop_name(self, stop_id: str) -> str:
        stop = self.stop_index.get(stop_id)
        if stop is None:
            return stop_id
        if stop.code:
            return f"{stop.name} ({stop.code})"
        return stop.name

    def _stops_for_selection(self, stop: Stop) -> list[Stop]:
        return [stop]

    def _selected_group_label(self) -> str:
        if not self.selected_stop:
            return "No stop selected"
        return self.selected_stop.label


def main() -> None:
    root = tk.Tk()
    BusDashboard(root)
    root.mainloop()
