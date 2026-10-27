"""Download Network Rail's daily SCHEDULE file and load it into SQLite (data/schedule.db).

Uses NR_USERNAME / NR_PASSWORD from .env or the environment.
Builds a new database beside the old one and swaps it in, so the server never sees a half-loaded file.
Run nightly:  python load_schedule.py
"""
import datetime as dt
import gzip
import json
import logging
import os
import sqlite3
import sys
import tempfile

import requests
from dotenv import load_dotenv

from schedule_db import DB_PATH, LONDON

SCHEDULE_URL = "https://publicdatafeeds.networkrail.co.uk/ntrod/CifFileAuthenticate?type=CIF_ALL_FULL_DAILY&day=toc-full"
log = logging.getLogger("railfeed.load")

SCHEMA = """
CREATE TABLE schedules (uid TEXT, start TEXT, end TEXT, days TEXT, stp TEXT, head TEXT, atoc TEXT,
                        category TEXT, status TEXT, origin TEXT, dest TEXT, dep INTEGER, arr INTEGER, stops TEXT);
CREATE TABLE tiplocs (tiploc TEXT PRIMARY KEY, name TEXT);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""


def parse_time(v):
    """'0615' or '0615H' (half minute) -> seconds after midnight."""
    v = (v or "").strip()
    if len(v) < 4 or not v[:4].isdigit():
        return None
    return int(v[:2]) * 3600 + int(v[2:4]) * 60 + (30 if v[4:5] == "H" else 0)


def schedule_row(s):
    stp = s.get("CIF_stp_indicator")
    if stp not in ("C", "O", "N", "P") or s.get("transaction_type") == "Delete":
        return None
    seg = s.get("schedule_segment") or {}
    if isinstance(seg, list):
        seg = seg[0] if seg else {}
    locs = seg.get("schedule_location") or []
    origin = dest = dep = arr = None
    stops = None
    if locs and s.get("train_status") in ("P", "1", "B", "5"):  # passenger only: freight headcodes are too reused to verify
        parts, prev = [], 0
        for loc in locs:
            a = parse_time(loc.get("arrival") or loc.get("pass") or loc.get("departure"))
            d = parse_time(loc.get("departure") or loc.get("pass") or loc.get("arrival"))
            if a is None or d is None:
                continue
            while a < prev:  # ran past midnight
                a += 86400
            prev = a
            while d < prev:
                d += 86400
            prev = d
            parts.append(f"{(loc.get('tiploc_code') or '').strip()},{a},{d}")
        stops = ";".join(parts)
    if locs:
        first, last = locs[0], locs[-1]
        origin, dest = (first.get("tiploc_code") or "").strip(), (last.get("tiploc_code") or "").strip()
        dep = parse_time(first.get("departure") or first.get("public_departure"))
        arr = parse_time(last.get("arrival") or last.get("public_arrival"))
        if dep is not None and arr is not None and arr < dep:
            arr += 86400  # ends after midnight
    return (s["CIF_train_uid"], s["schedule_start_date"], s["schedule_end_date"], s.get("schedule_days_runs") or "",
            stp, (seg.get("signalling_id") or "").strip(), s.get("atoc_code"), seg.get("CIF_train_category"),
            s.get("train_status"), origin, dest, dep, arr, stops)


def build(src, out, today=None):
    """Parse the JSON-lines schedule file `src` into a fresh SQLite database at `out`."""
    today = today or dt.datetime.now(LONDON).date()
    cutoff = (today - dt.timedelta(days=1)).isoformat()
    with open(src, "rb") as f:
        gz = f.read(2) == b"\x1f\x8b"
    tmp = out + ".new"
    if os.path.exists(tmp):
        os.remove(tmp)
    db = sqlite3.connect(tmp)
    db.executescript(SCHEMA)
    n = t = 0
    batch = []
    with (gzip.open if gz else open)(src, "rb") as f:
        for line in f:
            head = line[:40]
            try:
                if b"JsonScheduleV1" in head:
                    row = schedule_row(json.loads(line)["JsonScheduleV1"])
                    if row and row[2] >= cutoff:  # skip schedules that ended before yesterday
                        batch.append(row)
                        n += 1
                        if len(batch) >= 20000:
                            db.executemany("INSERT INTO schedules VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", batch)
                            batch.clear()
                elif b"TiplocV1" in head:
                    tp = json.loads(line)["TiplocV1"]
                    if tp.get("transaction_type") != "Delete" and tp.get("tiploc_code"):
                        name = (tp.get("tps_description") or "").strip().title()
                        db.execute("INSERT OR REPLACE INTO tiplocs VALUES (?,?)", (tp["tiploc_code"].strip(), name))
                        t += 1
            except (ValueError, KeyError, TypeError):
                continue
    if batch:
        db.executemany("INSERT INTO schedules VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", batch)
    db.execute("CREATE INDEX idx_head ON schedules(head)")
    db.execute("CREATE INDEX idx_key ON schedules(uid, start, stp)")
    db.execute("INSERT INTO meta VALUES ('loaded_at', ?)", (dt.datetime.now(LONDON).isoformat(),))
    db.commit()
    db.close()
    os.replace(tmp, out)  # atomic swap
    return n, t


def download(user, password, dest):
    with requests.get(SCHEDULE_URL, auth=(user, password), stream=True, timeout=(30, 300)) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)


def run():
    load_dotenv()
    user, password = os.getenv("NR_USERNAME"), os.getenv("NR_PASSWORD")
    if not user or not password:
        raise RuntimeError("Set NR_USERNAME and NR_PASSWORD in .env or the environment")
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "schedule")
        log.info("Downloading schedule...")
        download(user, password, path)
        log.info("Downloaded %.0f MB, loading into SQLite...", os.path.getsize(path) / 1e6)
        n, t = build(path, DB_PATH)
    log.info("Schedule loaded: %d schedules, %d locations", n, t)
    return n, t


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        run()
    except Exception as e:
        sys.exit(f"Schedule load failed: {e}")
