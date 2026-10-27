"""Read side of the SCHEDULE database (data/schedule.db, built by load_schedule.py).
Finds the timetable entry for a running train and describes it for the map."""
import datetime as dt
import os
import sqlite3
from zoneinfo import ZoneInfo

DB_PATH = os.getenv("SCHEDULE_DB", "data/schedule.db")
LONDON = ZoneInfo("Europe/London")
STP_RANK = {"O": 0, "N": 1, "P": 2}  # overlay beats new beats permanent

COLS = "uid, start, end, days, stp, head, atoc, category, status, origin, dest, dep, arr"

# Approximate ATOC code -> operator name; unknown codes are shown as-is.
OPERATORS = {
    "AW": "Transport for Wales", "CC": "c2c", "CH": "Chiltern Railways", "CS": "Caledonian Sleeper",
    "EM": "East Midlands Railway", "ES": "Eurostar", "GC": "Grand Central", "GN": "Great Northern",
    "GR": "LNER", "GW": "Great Western Railway", "GX": "Gatwick Express", "HT": "Hull Trains",
    "HX": "Heathrow Express", "IL": "Island Line", "LD": "Lumo", "LE": "Greater Anglia",
    "LM": "West Midlands Trains", "LO": "London Overground", "ME": "Merseyrail", "NT": "Northern",
    "SE": "Southeastern", "SN": "Southern", "SR": "ScotRail", "SW": "South Western Railway",
    "TL": "Thameslink", "TP": "TransPennine Express", "VT": "Avanti West Coast", "XC": "CrossCountry",
    "XR": "Elizabeth line", "ZZ": "Non-passenger / other",
}

# CIF train category -> readable type
CATEGORIES = {
    "OL": "London Underground", "OU": "Ordinary passenger (unadvertised)", "OO": "Ordinary passenger",
    "OS": "Staff train", "OW": "Mixed passenger", "XC": "Channel Tunnel", "XD": "Sleeper (Europe)",
    "XI": "International", "XR": "Motorail", "XU": "Express passenger (unadvertised)",
    "XX": "Express passenger", "XZ": "Sleeper", "BR": "Bus replacement", "BS": "Bus",
    "EE": "Empty coaching stock", "EL": "Empty coaching stock (Underground)", "ES": "Empty coaching stock & staff",
    "JJ": "Postal", "PM": "Postal", "PP": "Parcels", "PV": "Empty parcels vans", "DD": "Departmental",
    "DH": "Civil engineer", "DI": "Mechanised track", "DQ": "Test train", "DT": "Test train",
    "DY": "Departmental", "ZB": "Light locomotive", "ZZ": "Light locomotive",
}


def connect():
    if not os.path.exists(DB_PATH):
        return None
    return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)


def available():
    return os.path.exists(DB_PATH)


def current():
    """True if the database exists and has the calling-points column (older builds don't)."""
    db = connect()
    if db is None:
        return False
    try:
        db.execute("SELECT stops FROM schedules LIMIT 1")
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        db.close()


def service_date(tid, ts):
    """train_id ends in the day of the month the train was activated for."""
    try:
        dom = int(tid[-2:])
    except ValueError:
        return None
    local = dt.datetime.fromtimestamp(ts, LONDON).date()
    for off in (0, -1, 1):
        d = local + dt.timedelta(days=off)
        if d.day == dom:
            return d
    return None


def _runs_on(days, d):
    return days[d.weekday():d.weekday() + 1] == "1"


def _stop_times(stops, tiploc):
    out = []
    for part in stops.split(";"):
        tp, a, d = part.split(",")
        if tp == tiploc:
            out.append((int(a) + int(d)) / 2)
    return out


def _by_headcode(db, tid, ts, tiploc, delay):
    if not tiploc:
        return None  # can't verify the route, so don't guess
    d = service_date(tid, ts)
    if d is None:
        return None
    iso = d.isoformat()
    rel = ts - dt.datetime.combine(d, dt.time(), LONDON).timestamp()
    best = None
    rows = db.execute(f"SELECT {COLS}, stops FROM schedules WHERE head=? AND start<=? AND end>=? AND stp!='C'",
                      (tid[2:6], iso, iso))
    for row in rows:
        if not _runs_on(row[3], d) or not row[13]:
            continue
        # it must call at the station where the train was last seen, near the time it was due (allowing for lateness)
        diffs = [abs(rel - delay - t) for t in _stop_times(row[13], tiploc)]
        if not diffs or min(diffs) > 900:
            continue
        key = (STP_RANK.get(row[4], 9), min(diffs))
        if best is None or key < best[0]:
            best = (key, row[:13])
    if best is None:
        return None
    uid = best[1][0]
    for days, in db.execute("SELECT days FROM schedules WHERE uid=? AND stp='C' AND start<=? AND end>=?",
                            (uid, iso, iso)):
        if _runs_on(days, d):
            return None  # cancelled for that day
    return best[1]


def _name(db, tiploc):
    r = db.execute("SELECT name FROM tiplocs WHERE tiploc=?", (tiploc,)).fetchone()
    return r[0] if r and r[0] else tiploc


def _hhmm(secs):
    return None if secs is None else f"{secs // 3600 % 24:02d}:{secs % 3600 // 60:02d}"


def lookup(tid, act_key, ts, tiploc=None, delay=0):
    """Describe a running train from the timetable, or None if there's no match.
    act_key is (train_uid, schedule_start_date, schedule_type) from its activation message."""
    db = connect()
    if db is None:
        return None
    try:
        row = None
        if act_key and None not in act_key:
            row = db.execute(f"SELECT {COLS} FROM schedules WHERE uid=? AND start=? AND stp=?", act_key).fetchone()
        if row is None:
            row = _by_headcode(db, tid, ts, tiploc, delay)
        if row is None:
            return None
        _, _, _, _, stp, _, atoc, cat, status, origin, dest, dep, arr = row
        kind = CATEGORIES.get(cat, cat or "Unknown")
        if status in ("F", "2"):
            kind = "Freight"
        return {
            "operator": OPERATORS.get(atoc, atoc or "Unknown"),
            "type": kind,
            "origin": _name(db, origin),
            "destination": _name(db, dest),
            "departs": _hhmm(dep),
            "arrives": _hhmm(arr),
            "matched": "activation" if act_key and row[4] == act_key[2] else "headcode",
        }
    finally:
        db.close()
