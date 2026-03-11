# Muenster Bus Board

Desktop live board for Muenster buses, backed by Python and the public Stadtwerke Muenster busradar API.

## What it does

- Loads the public stop list from `https://rest.busradar.conterra.de/prod/haltestellen`
- Shows live departures for your chosen stop via `https://rest.busradar.conterra.de/prod/haltestellen/{stop_id}/abfahrten`
- Tracks live bus positions via `https://rest.busradar.conterra.de/prod/fahrzeuge`
- Starts a local desktop shell using `pywebview` on top of the local web app
- Falls back to an external Chromium browser if the embedded shell is not available
- Renders a real street map with OpenStreetMap tiles, live bus markers, and hover tooltips directly under the live header
- Each live platform/direction is treated as its own stop, so you can track the exact side you use
- Filters the view to your preferred lines, defaulting to `5, 11, 22`
- Saves your preferred stop and filters to `bus_settings.json`

## Run it

```powershell
python -m pip install -e .
python app.py
```

This starts a local server at `http://127.0.0.1:8765` or the next free port and opens an embedded desktop window when `pywebview` is installed. On Windows, that uses the system WebView2 runtime, which is Chromium-based. If the embedded shell cannot start, the app falls back to Edge/Chrome/Chromium/Brave or your default browser.

If you want the package-style entry point too:

```powershell
python -m bus_app
```

## First use

1. Start the app.
2. Search for your usual stop by name, stop code, or stop ID.
3. Click the stop in the left panel.
4. Keep the default line filter `5,11,22` or change it.
5. The dashboard will auto-refresh every 15 seconds by default.

## Notes

- The desktop shell is powered by `pywebview`; the UI itself is still served by the local Python HTTP server.
- The map uses Leaflet from a CDN plus OpenStreetMap raster tiles.
- Google Maps was not used here because that would require Google Maps Platform integration and the related API key/billing terms.
- Tile attribution is shown inside the map view.
