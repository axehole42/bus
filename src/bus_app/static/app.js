const state = {
  settings: null,
  selectedStop: null,
  stops: [],
  snapshot: null,
  map: null,
  stopMarker: null,
  vehicleLayer: null,
  selectionChangeToken: 0,
  searchTimer: null,
  refreshTimer: null,
  activeStopIdForCenter: null,
  clockTimer: null,
  uiScale: 1,
};

const UI_SCALE_KEY = "bus_board_ui_scale";
const MIN_UI_SCALE = 0.8;
const MAX_UI_SCALE = 1.35;
const UI_SCALE_STEP = 0.05;

const refs = {
  searchInput: document.getElementById("searchInput"),
  stopResults: document.getElementById("stopResults"),
  selectedStopText: document.getElementById("selectedStopText"),
  linesInput: document.getElementById("linesInput"),
  refreshInput: document.getElementById("refreshInput"),
  lookaheadInput: document.getElementById("lookaheadInput"),
  maxDeparturesInput: document.getElementById("maxDeparturesInput"),
  refreshButton: document.getElementById("refreshButton"),
  saveButton: document.getElementById("saveButton"),
  statusText: document.getElementById("statusText"),
  heroStop: document.getElementById("heroStop"),
  nextDeparture: document.getElementById("nextDeparture"),
  vehicleCount: document.getElementById("vehicleCount"),
  currentTime: document.getElementById("currentTime"),
  currentDate: document.getElementById("currentDate"),
  zoomLevel: document.getElementById("zoomLevel"),
  lastUpdated: document.getElementById("lastUpdated"),
  heroSubtitle: document.getElementById("heroSubtitle"),
  linePills: document.getElementById("linePills"),
  mapCoverageNote: document.getElementById("mapCoverageNote"),
  departuresBody: document.getElementById("departuresBody"),
  vehiclesBody: document.getElementById("vehiclesBody"),
  zoomInButton: document.getElementById("zoomInButton"),
  zoomOutButton: document.getElementById("zoomOutButton"),
  centerMapButton: document.getElementById("centerMapButton"),
};

function setStatus(message, tone = "default") {
  refs.statusText.textContent = message;
  refs.statusText.classList.toggle("error-text", tone === "error");
}

async function apiGet(path) {
  const response = await fetch(path, { cache: "no-store" });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || `Request failed for ${path}`);
  }
  return payload;
}

async function apiPost(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || `Request failed for ${path}`);
  }
  return payload;
}

function parseLines(value) {
  const entries = value
    .split(",")
    .map((part) => part.trim().toUpperCase())
    .filter(Boolean);
  return [...new Set(entries.length ? entries : ["5", "11", "22"])];
}

function escapeHtmlAttribute(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("\"", "&quot;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function lineColor(line) {
  if (line === "5") return "#14B8A6";
  if (line === "11") return "#F97316";
  if (line === "22") return "#2563EB";
  return "#475569";
}

function formatDelay(seconds) {
  if (seconds === 0) return "on time";
  const minutes = Math.ceil(Math.abs(seconds) / 60);
  return `${seconds > 0 ? "+" : "-"}${minutes} min`;
}

function delayTone(seconds) {
  if (seconds > 60) return "late";
  if (seconds < -60) return "early";
  return "ontime";
}

function delayBadge(seconds) {
  const tone = delayTone(seconds);
  const label = formatDelay(seconds);
  return `<span class="delay-badge delay-badge--${tone}">${label}</span>`;
}

function formatClock(timestamp) {
  if (!timestamp) return "-";
  return new Date(timestamp * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function formatEta(timestamp, nowSeconds) {
  if (!timestamp) return "-";
  const delta = timestamp - nowSeconds;
  if (delta <= 0) return "due";
  return `${Math.ceil(delta / 60)} min`;
}

function formatDistance(distanceMeters) {
  if (distanceMeters == null) return "-";
  if (distanceMeters < 1000) return `${Math.round(distanceMeters)} m`;
  return `${(distanceMeters / 1000).toFixed(1)} km`;
}

function formatOccupancy(occupancy) {
  if (!occupancy) return "-";
  const normalized = occupancy.toLowerCase();
  if (normalized.includes("schwach")) return "Low";
  if (normalized.includes("mittel")) return "Med";
  if (normalized.includes("stark")) return "High";
  if (normalized.includes("unbekannt")) return "-";
  return occupancy;
}

function describeDirection(direction) {
  const normalized = (direction || "").trim();
  const folded = normalized.toLowerCase();

  if (folded.includes("ein")) {
    return {
      shortLabel: "Inward",
      longLabel: "toward Muenster city centre",
      rawLabel: normalized,
    };
  }

  if (folded.includes("aus")) {
    return {
      shortLabel: "Outward",
      longLabel: "away from Muenster city centre",
      rawLabel: normalized,
    };
  }

  return {
    shortLabel: normalized || "Unknown",
    longLabel: "direction not published",
    rawLabel: normalized,
  };
}

function formatDirectionText(direction) {
  const info = describeDirection(direction);
  if (info.rawLabel) {
    return `${info.shortLabel} (${info.rawLabel})`;
  }
  return info.shortLabel;
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function applyUiScale(scale, options = {}) {
  const normalized = Number(clamp(scale, MIN_UI_SCALE, MAX_UI_SCALE).toFixed(2));
  state.uiScale = normalized;
  document.documentElement.style.setProperty("--ui-scale", String(normalized));
  refs.zoomLevel.textContent = `${Math.round(normalized * 100)}%`;

  if (options.persist !== false) {
    window.localStorage.setItem(UI_SCALE_KEY, String(normalized));
  }

  invalidateMapSoon();
}

function adjustUiScale(direction) {
  const nextScale = state.uiScale + direction * UI_SCALE_STEP;
  applyUiScale(nextScale);
}

function restoreUiScale() {
  const stored = Number(window.localStorage.getItem(UI_SCALE_KEY));
  if (Number.isFinite(stored)) {
    applyUiScale(stored, { persist: false });
    return;
  }
  applyUiScale(1, { persist: false });
}

function updateCurrentTime() {
  const now = new Date();
  refs.currentTime.textContent = now.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
  refs.currentDate.textContent = now.toLocaleDateString([], {
    weekday: "short",
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

function startClock() {
  updateCurrentTime();
  if (state.clockTimer) {
    window.clearInterval(state.clockTimer);
  }
  state.clockTimer = window.setInterval(updateCurrentTime, 1000);
}

function createBusIcon(line) {
  return L.divIcon({
    className: "",
    html: `<div class="bus-marker" style="--marker-color:${lineColor(line)}">${line}</div>`,
    iconSize: [32, 32],
    iconAnchor: [16, 16],
  });
}

function createStopIcon() {
  return L.divIcon({
    className: "",
    html: `<div class="stop-marker"></div>`,
    iconSize: [24, 24],
    iconAnchor: [12, 12],
  });
}

function busTooltip(vehicle) {
  const selectedStop = state.selectedStop;
  const selectedDirection = describeDirection(selectedStop ? selectedStop.direction : "");

  return `
    <div class="tooltip-card">
      <strong>Line ${vehicle.line} | Bus ${vehicle.vehicle_id}</strong>
      <span>Route: ${vehicle.direction}</span>
      <span>Delay: ${formatDelay(vehicle.delay_seconds)}</span>
      <span>Current: ${vehicle.current_stop_name}</span>
      <span>Next: ${vehicle.next_stop_name}</span>
      <span>Tracking stop: ${selectedStop ? `${selectedStop.name} ${selectedStop.code || selectedStop.stop_id}` : "-"}</span>
      <span>Selected side: ${formatDirectionText(selectedStop ? selectedStop.direction : "")}</span>
      <span>${selectedDirection.longLabel}</span>
      <span>Distance to stop: ${formatDistance(vehicle.distance_to_selected_m)}</span>
    </div>
  `;
}

function stopTooltip(stop) {
  const direction = describeDirection(stop.direction);

  return `
    <div class="tooltip-card">
      <strong>${stop.name}</strong>
      <span>Platform: ${stop.code || stop.stop_id}</span>
      <span>Stop ID: ${stop.stop_id}</span>
      <span>Direction: ${formatDirectionText(stop.direction)}</span>
      <span>${direction.longLabel}</span>
      <span>${stop.latitude.toFixed(5)}, ${stop.longitude.toFixed(5)}</span>
    </div>
  `;
}

function invalidateMapSoon() {
  if (!state.map) return;
  window.requestAnimationFrame(() => {
    state.map.invalidateSize(false);
  });
}

function ensureMap() {
  if (state.map) return;

  state.map = L.map("map", {
    zoomControl: false,
    preferCanvas: true,
    minZoom: 13,
    maxZoom: 19,
  }).setView([51.96236, 7.62571], 15);

  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap contributors",
    maxZoom: 19,
    crossOrigin: true,
  }).addTo(state.map);

  state.vehicleLayer = L.layerGroup().addTo(state.map);
  window.setTimeout(invalidateMapSoon, 0);
}

function renderLinePills(lines) {
  refs.linePills.innerHTML = "";
  for (const line of lines) {
    const pill = document.createElement("div");
    pill.className = "line-pill";
    pill.innerHTML = `
      <span class="line-chip" style="color:${lineColor(line)}">
        <span class="line-chip__dot"></span>
        Line ${line}
      </span>
    `;
    refs.linePills.appendChild(pill);
  }
}

function renderStops() {
  refs.stopResults.innerHTML = "";

  if (!state.stops.length) {
    refs.stopResults.innerHTML = `<div class="empty-state">No matching stops.</div>`;
    return;
  }

  for (const stop of state.stops) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "stop-button";
    if (state.selectedStop && state.selectedStop.stop_id === stop.stop_id) {
      button.classList.add("is-selected");
    }
    button.innerHTML = `
      <span class="stop-button__title">${stop.name}</span>
      <span class="stop-button__meta">${stop.code || stop.stop_id}${stop.direction ? ` | ${stop.direction}` : ""}</span>
    `;
    button.addEventListener("click", () => selectStop(stop, { centerMap: true }));
    refs.stopResults.appendChild(button);
  }
}

function renderDepartures() {
  refs.departuresBody.innerHTML = "";
  if (!state.snapshot || !state.snapshot.departures.length) {
    refs.departuresBody.innerHTML = `<tr><td colspan="5" class="empty-state">No departures for the selected filters.</td></tr>`;
    refs.nextDeparture.textContent = "-";
    return;
  }

  const now = state.snapshot.server_time;
  const firstDeparture = state.snapshot.departures[0];
  refs.nextDeparture.textContent = `${firstDeparture.line} in ${formatEta(firstDeparture.actual_departure, now)}`;

  for (const departure of state.snapshot.departures) {
    const direction = describeDirection(departure.direction_label);
    const hoverLabel = escapeHtmlAttribute(
      `${departure.platform_name} ${departure.platform_label} | ${formatDirectionText(departure.direction_label)} | ${direction.longLabel}`
    );
    const row = document.createElement("tr");
    row.innerHTML = `
      <td><span class="line-chip" style="color:${lineColor(departure.line)}"><span class="line-chip__dot"></span>${departure.line}</span></td>
      <td>${formatEta(departure.actual_departure, now)}</td>
      <td class="times-cell">
        <span class="times-cell__live">${formatClock(departure.actual_departure)}</span>
        <span class="times-cell__scheduled">Sched ${formatClock(departure.scheduled_departure)}</span>
      </td>
      <td>${delayBadge(departure.delay_seconds)}</td>
      <td class="destination-cell" title="${hoverLabel}">
        <span class="destination-cell__name">${departure.destination}</span>
        <span class="destination-cell__meta">${departure.platform_label} | ${direction.shortLabel}</span>
      </td>
    `;
    refs.departuresBody.appendChild(row);
  }
}

function renderVehicles() {
  refs.vehiclesBody.innerHTML = "";
  if (!state.snapshot || !state.snapshot.vehicles.length) {
    refs.vehiclesBody.innerHTML = `<tr><td colspan="8" class="empty-state">No active vehicles for the tracked lines.</td></tr>`;
    refs.vehicleCount.textContent = "0";
    return;
  }

  refs.vehicleCount.textContent = String(state.snapshot.vehicles.length);

  for (const vehicle of state.snapshot.vehicles) {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td><span class="line-chip" style="color:${lineColor(vehicle.line)}"><span class="line-chip__dot"></span>${vehicle.line}</span></td>
      <td>${vehicle.vehicle_id}</td>
      <td>${formatDistance(vehicle.distance_to_selected_m)}</td>
      <td>${delayBadge(vehicle.delay_seconds)}</td>
      <td>${vehicle.current_stop_name}</td>
      <td>${vehicle.next_stop_name}</td>
      <td>${vehicle.direction}</td>
      <td>${vehicle.latitude.toFixed(5)}, ${vehicle.longitude.toFixed(5)}</td>
    `;
    refs.vehiclesBody.appendChild(row);
  }
}

function renderCoverageNote(snapshot) {
  const departureLines = new Set(snapshot.departures.map((item) => item.line));
  const liveVehicleLines = new Set(snapshot.vehicles.map((item) => item.line));
  const missingLiveLines = snapshot.lines.filter((line) => departureLines.has(line) && !liveVehicleLines.has(line));

  if (!missingLiveLines.length) {
    refs.mapCoverageNote.textContent = "Live position feed available for all visible departure lines.";
    refs.mapCoverageNote.classList.remove("coverage-note--warn");
    return;
  }

  refs.mapCoverageNote.textContent = `No live position data currently available for line${missingLiveLines.length > 1 ? "s" : ""} ${missingLiveLines.join(", ")}. Departures are still shown from the stop board feed.`;
  refs.mapCoverageNote.classList.add("coverage-note--warn");
}

function renderMap(centerMap = false) {
  ensureMap();
  if (!state.selectedStop || !state.snapshot) return;

  if (!state.stopMarker) {
    state.stopMarker = L.marker([state.selectedStop.latitude, state.selectedStop.longitude], {
      icon: createStopIcon(),
      keyboard: false,
    }).addTo(state.map);
  }

  state.stopMarker
    .setLatLng([state.selectedStop.latitude, state.selectedStop.longitude])
    .bindTooltip(stopTooltip(state.selectedStop), {
      direction: "top",
      sticky: true,
      offset: [0, -12],
      className: "stop-tooltip",
      opacity: 1,
    });

  state.vehicleLayer.clearLayers();
  for (const vehicle of state.snapshot.vehicles) {
    L.marker([vehicle.latitude, vehicle.longitude], {
      icon: createBusIcon(vehicle.line),
      keyboard: false,
    })
      .bindTooltip(busTooltip(vehicle), {
        direction: "top",
        sticky: true,
        offset: [0, -14],
        className: "bus-tooltip",
        opacity: 1,
      })
      .addTo(state.vehicleLayer);
  }

  if (centerMap || state.activeStopIdForCenter !== state.selectedStop.stop_id) {
    state.map.setView([state.selectedStop.latitude, state.selectedStop.longitude], 16, { animate: true });
    state.activeStopIdForCenter = state.selectedStop.stop_id;
  }

  invalidateMapSoon();
}

function applySnapshot(snapshot, centerMap = false) {
  state.snapshot = snapshot;
  refs.heroStop.textContent = state.selectedStop ? state.selectedStop.name : "No stop";
  refs.lastUpdated.textContent = new Date(snapshot.server_time * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
  refs.heroSubtitle.textContent = `${snapshot.departures.length} departures and ${snapshot.vehicles.length} live vehicles on lines ${snapshot.lines.join(", ")}.`;
  renderLinePills(snapshot.lines);
  renderDepartures();
  renderVehicles();
  renderCoverageNote(snapshot);
  renderMap(centerMap);
}

async function fetchStops(query) {
  const token = ++state.selectionChangeToken;
  const payload = await apiGet(`/api/stops?q=${encodeURIComponent(query)}`);
  if (token !== state.selectionChangeToken) return;
  state.stops = payload.items;
  renderStops();
}

function scheduleRefresh(refreshSeconds) {
  if (state.refreshTimer) {
    window.clearTimeout(state.refreshTimer);
  }
  state.refreshTimer = window.setTimeout(() => {
    refreshSnapshot({ centerMap: false });
  }, refreshSeconds * 1000);
}

async function refreshSnapshot(options = {}) {
  if (!state.selectedStop) {
    setStatus("Select a stop first.");
    return;
  }

  const lines = parseLines(refs.linesInput.value);
  const refreshSeconds = Math.max(5, Number(refs.refreshInput.value || 15));
  const lookaheadSeconds = Math.max(600, Number(refs.lookaheadInput.value || 90) * 60);
  const maxDepartures = Math.max(1, Number(refs.maxDeparturesInput.value || 20));

  setStatus("Refreshing live data...");
  try {
    const snapshot = await apiGet(
      `/api/snapshot?stop_id=${encodeURIComponent(state.selectedStop.stop_id)}&lines=${encodeURIComponent(lines.join(","))}&lookahead_seconds=${lookaheadSeconds}&max_departures=${maxDepartures}`
    );
    applySnapshot(snapshot, options.centerMap === true);
    setStatus(`Tracking ${state.selectedStop.label || state.selectedStop.name}.`);
    scheduleRefresh(refreshSeconds);
  } catch (error) {
    setStatus(error.message, "error");
  }
}

async function saveDefaults() {
  if (!state.selectedStop) {
    setStatus("Select a stop first.", "error");
    return;
  }

  try {
    const payload = await apiPost("/api/settings", {
      stop_id: state.selectedStop.stop_id,
      stop_search: refs.searchInput.value,
      lines: refs.linesInput.value,
      refresh_seconds: Number(refs.refreshInput.value || 15),
      lookahead_seconds: Number(refs.lookaheadInput.value || 90) * 60,
      max_departures: Number(refs.maxDeparturesInput.value || 20),
    });
    state.settings = payload.settings;
    setStatus("Saved defaults to bus_settings.json.");
  } catch (error) {
    setStatus(error.message, "error");
  }
}

function selectStop(stop, options = {}) {
  state.selectedStop = stop;
  refs.selectedStopText.textContent = stop.label || stop.name;
  refs.heroStop.textContent = stop.name;
  renderStops();
  refreshSnapshot({ centerMap: options.centerMap === true });
}

function bindEvents() {
  refs.searchInput.addEventListener("input", () => {
    if (state.searchTimer) {
      window.clearTimeout(state.searchTimer);
    }
    state.searchTimer = window.setTimeout(() => {
      fetchStops(refs.searchInput.value).catch((error) => setStatus(error.message, "error"));
    }, 120);
  });

  refs.refreshButton.addEventListener("click", () => refreshSnapshot({ centerMap: false }));
  refs.saveButton.addEventListener("click", saveDefaults);
  refs.zoomInButton.addEventListener("click", () => state.map && state.map.zoomIn());
  refs.zoomOutButton.addEventListener("click", () => state.map && state.map.zoomOut());
  refs.centerMapButton.addEventListener("click", () => {
    if (!state.map || !state.selectedStop) return;
    state.map.setView([state.selectedStop.latitude, state.selectedStop.longitude], Math.max(state.map.getZoom(), 16), {
      animate: true,
    });
  });
  window.addEventListener(
    "wheel",
    (event) => {
      if (!event.ctrlKey) return;
      event.preventDefault();
      adjustUiScale(event.deltaY < 0 ? 1 : -1);
    },
    { passive: false }
  );
  window.addEventListener("keydown", (event) => {
    if (!event.ctrlKey) return;
    if (event.key === "0") {
      event.preventDefault();
      applyUiScale(1);
    }
  });
  window.addEventListener("resize", invalidateMapSoon);
}

async function init() {
  restoreUiScale();
  ensureMap();
  bindEvents();
  startClock();

  try {
    const config = await apiGet("/api/config");
    state.settings = config.settings;
    refs.searchInput.value = config.settings.stop_search || "";
    refs.linesInput.value = config.settings.monitored_lines.join(",");
    refs.refreshInput.value = String(config.settings.refresh_seconds);
    refs.lookaheadInput.value = String(config.settings.lookahead_seconds / 60);
    refs.maxDeparturesInput.value = String(config.settings.max_departures);
    renderLinePills(config.settings.monitored_lines);

    await fetchStops(refs.searchInput.value);

    if (config.selected_stop) {
      selectStop(config.selected_stop, { centerMap: true });
    } else {
      setStatus("Select a stop to start the live board.");
    }
  } catch (error) {
    setStatus(error.message, "error");
  }
}

window.addEventListener("load", init);
window.addEventListener("beforeunload", () => {
  if (state.clockTimer) {
    window.clearInterval(state.clockTimer);
  }
});
