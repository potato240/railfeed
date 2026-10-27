# railfeed

Live map of trains in Great Britain from Network Rail open data. See CLAUDE.md for the spec.

Contains information of Network Rail Infrastructure Limited licensed under the Open Government Licence.

## Files
- `build_stations.py`: builds `data/stations.json` (STANOX, name, CRS, lat/lon) from CORPUS + NaPTAN
- `server.py`: holds the STOMP feed connection, keeps latest train positions, serves the map and `/events` (SSE)
- `static/index.html`: Leaflet map
- `deploy/`: systemd unit and nginx site

## Run locally
    python -m venv .venv
    .venv/bin/pip install -r requirements.txt      # Windows: .venv\Scripts\pip
    cp .env.example .env                           # add your own Network Rail login
    .venv/bin/python build_stations.py
    .venv/bin/python server.py                     # http://127.0.0.1:8080

## Deploy (Coolify)
New Resource > Public Repository > https://github.com/potato240/railfeed, branch `main`, build pack Dockerfile, port 3000.
Set domain `https://<subdomain>.stevens-hall.com`, add env vars `NR_USERNAME` and `NR_PASSWORD`, deploy.
The container builds `data/stations.json` on start. Check `/status` on the site for train and message counts.

## Timetable (SCHEDULE) data
`load_schedule.py` downloads Network Rail's daily full timetable with the same login and loads it into SQLite (`data/schedule.db`),
swapping the new file in atomically. The server reads it when a train is clicked, to show operator, type, origin and destination.
- The server runs the load itself on first start if the database is missing.
- Nightly refresh: in Coolify, open the app > **Scheduled Tasks** > Add. Command `python load_schedule.py`, frequency `30 4 * * *`, container = the app.
- Optional: in Coolify > **Storages**, add a persistent volume at `/app/data`, so the database survives redeploys without a re-download.
- Not covered: VSTP (short-notice) trains have no entry in this file, so they show "No timetable match".

## Track geometry (dots follow the rails)
`track/railgraph.json.gz` is a compact graph of Great Britain's railway built from OpenStreetMap by `build_tracks.py`
(downloads rail lines from Overpass, snaps NaPTAN stations onto them; about 30 minutes, needs no login, resumable).
It is committed to the repo, so deploys don't need to rebuild it. Re-run `python build_tracks.py` occasionally to refresh.
`tracks.py` finds the shortest path along the track between two stations; the server serves it at `/route/<from tiploc>/<to tiploc>`
and the browser moves each dot along it. If a pair can't be routed, the dot moves in a straight line instead.
Track data (c) OpenStreetMap contributors, available under the ODbL (https://www.openstreetmap.org/copyright).
