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

## Deploy (Ubuntu)
    sudo useradd --system --home /opt/railfeed --shell /usr/sbin/nologin railfeed
    sudo git clone https://github.com/potato240/railfeed /opt/railfeed
    cd /opt/railfeed && sudo python3 -m venv .venv && sudo .venv/bin/pip install -r requirements.txt
    sudo cp .env.example .env && sudo nano .env    # add the login
    sudo chown -R railfeed:railfeed /opt/railfeed && sudo chmod 600 .env
    sudo -u railfeed .venv/bin/python build_stations.py
    sudo cp deploy/railfeed.service /etc/systemd/system/ && sudo systemctl enable --now railfeed
    sudo cp deploy/nginx.conf /etc/nginx/sites-available/railfeed   # edit server_name first
    sudo ln -s /etc/nginx/sites-available/railfeed /etc/nginx/sites-enabled/ && sudo nginx -t && sudo systemctl reload nginx

Check: `curl localhost:8080/status` shows train and message counts.
