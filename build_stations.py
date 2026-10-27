"""Build data/stations.json: STANOX -> name, CRS, lat, lon.

CORPUS (Network Rail, needs login) gives STANOX/CRS/name.
NaPTAN (DfT, open) gives lat/lon for rail stations (ATCO 9100 + TIPLOC).
Run once, and again whenever you want fresh reference data.
"""
import csv
import gzip
import io
import json
import os
import sys

import requests
from dotenv import load_dotenv

CORPUS_URL = "https://publicdatafeeds.networkrail.co.uk/ntrod/SupportingFileAuthenticate?type=CORPUS"
NAPTAN_URL = "https://naptan.api.dft.gov.uk/v1/access-nodes?atcoAreaCodes=910&dataFormat=csv"


def clean(v):
    return (v or "").strip()


def load_corpus(user, password):
    r = requests.get(CORPUS_URL, auth=(user, password), timeout=60)
    r.raise_for_status()
    raw = r.content
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return json.loads(raw)["TIPLOCDATA"]


def load_naptan():
    r = requests.get(NAPTAN_URL, timeout=120)
    r.raise_for_status()
    positions = {}
    for row in csv.DictReader(io.StringIO(r.content.decode("utf-8-sig"))):
        code = row["ATCOCode"]
        if not code.startswith("9100") or not row["Latitude"]:
            continue
        positions[code[4:]] = (float(row["Latitude"]), float(row["Longitude"]))
    return positions


def main():
    load_dotenv()
    user, password = os.getenv("NR_USERNAME"), os.getenv("NR_PASSWORD")
    if not user or not password:
        sys.exit("Set NR_USERNAME and NR_PASSWORD in .env")

    corpus = load_corpus(user, password)
    positions = load_naptan()

    stations = {}
    for row in corpus:
        stanox, crs, tiploc = clean(row.get("STANOX")), clean(row.get("3ALPHA")), clean(row.get("TIPLOC"))
        if not stanox or not crs:
            continue  # only passenger stations (rows with a CRS)
        pos = positions.get(tiploc)
        if not pos:
            continue
        stations[stanox] = {
            "name": clean(row.get("NLCDESC")).title(),
            "crs": crs,
            "tiploc": tiploc,
            "lat": pos[0],
            "lon": pos[1],
        }

    os.makedirs("data", exist_ok=True)
    with open("data/stations.json", "w", encoding="utf-8") as f:
        json.dump(stations, f, separators=(",", ":"))
    print(f"CORPUS rows: {len(corpus)}, NaPTAN rail stations: {len(positions)}, written: {len(stations)}")


if __name__ == "__main__":
    main()
