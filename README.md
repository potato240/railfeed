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
