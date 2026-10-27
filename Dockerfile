FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd --system --create-home railfeed && mkdir -p data && chown -R railfeed:railfeed /app
USER railfeed
ENV HOST=0.0.0.0 PORT=3000
EXPOSE 3000
# Build the station list on first start (needs NR_USERNAME/NR_PASSWORD), then run the server
CMD ["sh", "-c", "{ [ -f data/stations.json ] && grep -q '\"tiploc\"' data/stations.json; } || python build_stations.py; exec python server.py"]
