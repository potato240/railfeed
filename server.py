"""Holds the Network Rail STOMP connection, tracks the latest station event of
each train and pushes changes to browsers over Server-Sent Events.
The Network Rail login never leaves this process."""
import asyncio
import json
import logging
import os
import threading
import time

import stomp
from aiohttp import web
from dotenv import load_dotenv

import load_schedule
import schedule_db

load_dotenv()
NR_USER, NR_PASS = os.getenv("NR_USERNAME"), os.getenv("NR_PASSWORD")
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8080"))
TOPIC = "/topic/TRAIN_MVT_ALL_TOC"
MAX_AGE = 2 * 3600  # drop trains with no station event for 2 hours
FLUSH_EVERY = 1.0

log = logging.getLogger("railfeed")
with open("data/stations.json", encoding="utf-8") as f:
    STATIONS = json.load(f)

POS = {st["tiploc"]: (st["lat"], st["lon"], st["name"]) for st in STATIONS.values() if st.get("tiploc")}
trains = {}      # train_id -> dict
routes = {}      # train_id -> {"stops", "mid", "i"} once matched to a timetable entry, {"miss": time} if not, "pending" while matching
dirty = set()    # train_ids changed since last flush
removed = set()
acts = {}        # train_id -> ((train_uid, schedule_start_date, schedule_type), seen_at), from activation messages
clients = set()  # one asyncio.Queue per connected browser
stats = {"messages": 0, "last_message": None}


def signed_delay(t):
    """Seconds late (+) or early (-)."""
    sign = 1 if t.get("status") == "LATE" else -1 if t.get("status") == "EARLY" else 0
    return sign * t.get("var", 0) * 60


def update_leg(tid):
    """Work out where the train is heading from its latest station event and its matched timetable."""
    t, r = trains.get(tid), routes.get(tid)
    if not t or not isinstance(r, dict) or "stops" not in r:
        return
    stops = r["stops"]
    i = next((k for k in range(r["i"], len(stops)) if stops[k][0] == t.get("tp")), None)
    if i is None:
        i = next((k for k in range(len(stops)) if stops[k][0] == t.get("tp")), None)
    if i is None:  # not on the route we matched (diverted?): stop animating and match again next time
        routes.pop(tid, None)
        t["seg"] = t["nxt"] = None
        return
    r["i"] = i
    res = schedule_db.next_leg(stops, r["mid"], POS, i, t["ev"], t["ts"], signed_delay(t))
    t["seg"], t["nxt"] = res if res else (None, None)


async def match_route(tid, act, ts, tp, delay):
    """Match a train to its timetable entry off the event loop, then plan its movement."""
    try:
        res = await asyncio.to_thread(schedule_db.find, tid, act, ts, tp, delay)
    except Exception as e:
        log.error("Route match failed: %s", e)
        res = None
    if tid not in trains:
        routes.pop(tid, None)
        return
    if res and res[0][13]:
        routes[tid] = {"stops": schedule_db.parse_stops(res[0][13]), "mid": res[1], "i": 0}
    else:
        routes[tid] = {"miss": time.time()}
    update_leg(tid)
    dirty.add(tid)


def handle_message(m):
    """Runs on the event loop."""
    header, body = m.get("header", {}), m.get("body", {})
    mt, tid = header.get("msg_type"), body.get("train_id")
    if not tid:
        return
    stats["messages"] += 1
    stats["last_message"] = time.time()
    if mt == "0001":  # activation: tells us exactly which timetable entry this train is running
        acts[tid] = ((body.get("train_uid"), (body.get("schedule_start_date") or "")[:10],
                      body.get("schedule_type")), time.time())
        return
    if mt == "0002":  # cancellation
        routes.pop(tid, None)
        if trains.pop(tid, None):
            removed.add(tid)
            dirty.discard(tid)
        return
    if mt != "0003":  # movements only
        return
    st = STATIONS.get(body.get("loc_stanox"))
    if not st:
        return  # first version: stations only
    try:
        var = int(body.get("timetable_variation") or 0)
        ts = int(body["actual_timestamp"]) // 1000
    except (KeyError, ValueError):
        return
    trains[tid] = {
        "id": tid,
        "head": tid[2:6],
        "cls": tid[2:3],
        "lat": st["lat"], "lon": st["lon"],
        "stn": st["name"],
        "tp": st.get("tiploc"),
        "ev": body.get("event_type"),
        "var": var,
        "status": body.get("variation_status"),
        "ts": ts,
    }
    trains[tid]["seg"] = trains[tid]["nxt"] = None
    dirty.add(tid)
    removed.discard(tid)
    r = routes.get(tid)
    if isinstance(r, dict) and "stops" in r:
        update_leg(tid)
    elif r != "pending" and (r is None or time.time() - r["miss"] > 600) and schedule_db.available():
        routes[tid] = "pending"
        act = acts.get(tid)
        asyncio.ensure_future(match_route(tid, act[0] if act else None, ts, st.get("tiploc"), signed_delay(trains[tid])))


class Listener(stomp.ConnectionListener):
    def __init__(self, loop):
        self.loop = loop

    def on_message(self, frame):
        try:
            msgs = json.loads(frame.body)
        except ValueError:
            return
        for m in msgs:
            self.loop.call_soon_threadsafe(handle_message, m)

    def on_error(self, frame):
        log.error("STOMP error: %s", frame.body)

    def on_disconnected(self):
        log.warning("STOMP disconnected")


def stomp_thread(loop):
    """Keep one connection alive, reconnecting with backoff."""
    delay = 5
    while True:
        try:
            conn = stomp.Connection([("publicdatafeeds.networkrail.co.uk", 61618)],
                                    keepalive=True, heartbeats=(15000, 15000))
            conn.set_listener("", Listener(loop))
            conn.connect(NR_USER, NR_PASS, wait=True)
            conn.subscribe(destination=TOPIC, id=1, ack="auto")
            log.info("STOMP connected")
            delay = 5
            while conn.is_connected():
                time.sleep(5)
        except Exception as e:
            log.error("STOMP failure: %s", e)
        time.sleep(delay)
        delay = min(delay * 2, 120)


def initial_schedule_load():
    try:
        load_schedule.run()
    except Exception as e:
        log.error("Initial schedule load failed: %s", e)


async def flusher():
    ticks = 0
    while True:
        await asyncio.sleep(FLUSH_EVERY)
        ticks += 1
        if ticks % 600 == 0:  # forget activations older than 36 hours
            old = time.time() - 36 * 3600
            for tid in [t for t, (_, seen) in acts.items() if seen < old]:
                del acts[tid]
        cutoff = time.time() - MAX_AGE
        for tid in [t for t, v in trains.items() if v["ts"] < cutoff]:
            del trains[tid]
            routes.pop(tid, None)
            removed.add(tid)
            dirty.discard(tid)
        if not dirty and not removed:
            continue
        payload = json.dumps({"u": [trains[t] for t in dirty if t in trains], "r": list(removed), "now": time.time()})
        dirty.clear()
        removed.clear()
        for q in clients:
            if q.qsize() < 50:
                q.put_nowait(payload)


async def events(request):
    resp = web.StreamResponse(headers={
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",  # stop nginx buffering the stream
    })
    await resp.prepare(request)
    q = asyncio.Queue()
    clients.add(q)
    try:
        snapshot = json.dumps({"u": list(trains.values()), "r": [], "now": time.time()})
        await resp.write(f"data: {snapshot}\n\n".encode())
        while True:
            try:
                payload = await asyncio.wait_for(q.get(), timeout=15)
                await resp.write(f"data: {payload}\n\n".encode())
            except asyncio.TimeoutError:
                await resp.write(b": keepalive\n\n")
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        clients.discard(q)
    return resp


async def status(request):
    return web.json_response({"trains": len(trains), "clients": len(clients), "activations": len(acts),
                              "schedule_db": schedule_db.available(), **stats})


async def train_info(request):
    """Timetable details for one train, looked up when its marker is clicked."""
    tid = request.match_info["id"]
    t = trains.get(tid)
    if not t:
        raise web.HTTPNotFound()
    act = acts.get(tid)
    info = await asyncio.to_thread(schedule_db.lookup, tid, act[0] if act else None, t["ts"], t.get("tp"), signed_delay(t))
    return web.json_response({"info": info, "loading": not schedule_db.available()})


async def index(request):
    return web.FileResponse("static/index.html")


async def on_startup(app):
    loop = asyncio.get_running_loop()
    app["flusher"] = asyncio.create_task(flusher())
    if NR_USER and NR_PASS:
        threading.Thread(target=stomp_thread, args=(loop,), daemon=True).start()
        if not schedule_db.current():  # first run or old format; the nightly job keeps it fresh after that
            threading.Thread(target=initial_schedule_load, daemon=True).start()
    else:
        log.warning("NR_USERNAME/NR_PASSWORD not set: running without a live feed")


def make_app():
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/events", events)
    app.router.add_get("/status", status)
    app.router.add_get("/train/{id}", train_info)
    app.router.add_static("/static", "static")
    app.on_startup.append(on_startup)
    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    web.run_app(make_app(), host=HOST, port=PORT)
