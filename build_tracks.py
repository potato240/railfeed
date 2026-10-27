"""Build track/railgraph.json.gz: a compact graph of Great Britain's railway, from OpenStreetMap.

Run occasionally (the output is committed to the repo); needs no Network Rail login.
  python build_tracks.py [cache_dir]

1. Downloads railway=rail ways from Overpass in tiles (cached in cache_dir so it can resume).
2. Snaps each NaPTAN rail station to the nearest track on up to 6 different ways within 300 m.
3. Contracts the graph: only junctions, line ends and station snap points are kept as nodes;
   the track between them becomes one edge carrying a simplified polyline.

Track data (c) OpenStreetMap contributors, ODbL.
"""
import csv
import gzip
import io
import json
import math
import os
import sys
import time

import requests

OVERPASS = "https://overpass-api.de/api/interpreter"
UA = {"User-Agent": "railfeed/0.1 (https://github.com/potato240/railfeed)"}
NAPTAN_URL = "https://naptan.api.dft.gov.uk/v1/access-nodes?atcoAreaCodes=910&dataFormat=csv"
QUERY = ('[out:json][timeout:240];way["railway"="rail"]["service"!~"yard|siding|spur"]'
         '["usage"!~"industrial|military"]({s},{w},{n},{e});out geom;')
SNAP_M = 300       # station must be this close to a track
SNAP_WAYS = 6      # candidate tracks kept per station (parallel lines)
SIMPLIFY_M = 2.0   # polyline simplification tolerance
CELL = 0.004       # degrees, spatial grid for snapping
M_LAT = 111320.0


def tiles():
    """Land-covering lat/lon boxes: 1 x 2 degrees from Land's End to Shetland-ish, west coast to Lowestoft."""
    for s in [49.9 + i for i in range(9)]:
        for w in [-6.5 + 2 * i for i in range(5)]:
            yield s, w, s + 1, w + 2


def fetch_tile(box, cache):
    path = os.path.join(cache, "tile_%.1f_%.1f.json" % (box[0], box[1]))
    if os.path.exists(path):
        return path
    s, w, n, e = box
    for attempt in range(6):
        try:
            r = requests.post(OVERPASS, data={"data": QUERY.format(s=s, w=w, n=n, e=e)}, headers=UA,
                              timeout=300, stream=True)
            if r.status_code == 200:
                with open(path + ".part", "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
                os.replace(path + ".part", path)
                time.sleep(3)
                return path
            print("  tile", box, "HTTP", r.status_code, "- retrying", flush=True)
        except requests.RequestException as ex:
            print("  tile", box, ex, "- retrying", flush=True)
        time.sleep(30 * (attempt + 1))
    sys.exit(f"Could not fetch tile {box}")


def load_ways(cache):
    ways = {}
    for box in tiles():
        path = fetch_tile(box, cache)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        before = len(ways)
        for el in data.get("elements", []):
            geom = el.get("geometry")
            if el.get("type") != "way" or not geom or len(geom) < 2:
                continue
            pts = []
            for p in geom:
                q = (round(p["lat"] * 1e7), round(p["lon"] * 1e7))
                if not pts or pts[-1] != q:
                    pts.append(q)
            if len(pts) >= 2:
                ways[el["id"]] = pts
        print("tile %s: +%d ways (total %d)" % (box[:2], len(ways) - before, len(ways)), flush=True)
    return ways


def load_stations():
    r = requests.get(NAPTAN_URL, headers=UA, timeout=120)
    r.raise_for_status()
    out = {}
    for row in csv.DictReader(io.StringIO(r.content.decode("utf-8-sig"))):
        code = row["ATCOCode"]
        if code.startswith("9100") and row["Latitude"]:
            out[code[4:]] = (float(row["Latitude"]), float(row["Longitude"]), row["CommonName"])
    return out


def snap_stations(ways, stations):
    """-> {tiploc: [(way_id, point, distance_m), ...]} nearest point on each nearby way, best first."""
    grid = {}
    for tp, (lat, lon, _) in stations.items():
        grid.setdefault((int(lat / CELL), int(lon / CELL)), []).append((tp, lat, lon, math.cos(math.radians(lat))))
    best = {}
    for wid, pts in ways.items():
        for p in pts:
            plat, plon = p[0] / 1e7, p[1] / 1e7
            cx, cy = int(plat / CELL), int(plon / CELL)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for tp, slat, slon, cs in grid.get((cx + dx, cy + dy), ()):
                        d = math.hypot((plat - slat) * M_LAT, (plon - slon) * M_LAT * cs)
                        if d <= SNAP_M:
                            k = (tp, wid)
                            if k not in best or d < best[k][1]:
                                best[k] = (p, d)
    out = {}
    for (tp, wid), (p, d) in best.items():
        out.setdefault(tp, []).append((wid, p, d))
    return {tp: sorted(v, key=lambda x: x[2])[:SNAP_WAYS] for tp, v in out.items()}


def haversine(a, b):
    la1, lo1, la2, lo2 = a[0] / 1e7, a[1] / 1e7, b[0] / 1e7, b[1] / 1e7
    x = (lo2 - lo1) * math.cos(math.radians((la1 + la2) / 2))
    return math.hypot(la2 - la1, x) * M_LAT


def simplify(pts):
    """Douglas-Peucker on a list of (ilat, ilon); keeps the ends."""
    if len(pts) <= 2:
        return pts
    c = math.cos(math.radians(pts[0][0] / 1e7))
    xy = [(p[1] / 1e7 * M_LAT * c, p[0] / 1e7 * M_LAT) for p in pts]
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        ax, ay = xy[a]
        bx, by = xy[b]
        dx, dy = bx - ax, by - ay
        norm = math.hypot(dx, dy)
        far, fi = -1.0, None
        for i in range(a + 1, b):
            px, py = xy[i]
            d = math.hypot(px - ax, py - ay) if norm == 0 else abs(dx * (ay - py) - (ax - px) * dy) / norm
            if d > far:
                far, fi = d, i
        if far > SIMPLIFY_M:
            keep[fi] = True
            stack += [(a, fi), (fi, b)]
    return [p for p, k in zip(pts, keep) if k]


def build(ways, snaps):
    protected = {p for cands in snaps.values() for _, p, _ in cands}
    deg, endpoint = {}, set()
    for pts in ways.values():
        for k, p in enumerate(pts):
            deg[p] = deg.get(p, 0) + (k > 0) + (k < len(pts) - 1)
        endpoint.add(pts[0])
        endpoint.add(pts[-1])
    keep = {p for p, d in deg.items() if d != 2} | endpoint | protected
    ids, nodes, edges = {}, [], []

    def node(p):
        if p not in ids:
            ids[p] = len(nodes)
            nodes.append([round(p[0] / 1e7, 5), round(p[1] / 1e7, 5)])
        return ids[p]

    for pts in ways.values():
        start = 0
        for k in range(1, len(pts)):
            if pts[k] in keep:
                seg = pts[start:k + 1]
                length = sum(haversine(seg[i], seg[i + 1]) for i in range(len(seg) - 1))
                line = simplify(seg)
                flat = [round(v / 1e7, 5) for p in line for v in p]
                edges.append([node(seg[0]), node(seg[-1]), round(length), flat])
                start = k
    stations = {}
    for tp, cands in snaps.items():
        seen, lst = set(), []
        for _, p, d in cands:
            n = ids.get(p)
            if n is not None and n not in seen:
                seen.add(n)
                lst.append([n, round(d)])
        if lst:
            stations[tp] = lst
    return {"nodes": nodes, "edges": edges, "stations": stations}


def main():
    cache = sys.argv[1] if len(sys.argv) > 1 else "build_cache"
    os.makedirs(cache, exist_ok=True)
    ways = load_ways(cache)
    stations = load_stations()
    print("ways:", len(ways), "points:", sum(len(w) for w in ways.values()), "stations:", len(stations), flush=True)
    snaps = snap_stations(ways, stations)
    print("stations snapped to track:", len(snaps), "of", len(stations), flush=True)
    graph = build(ways, snaps)
    print("graph: %d nodes, %d edges, %d points in edges" % (
        len(graph["nodes"]), len(graph["edges"]), sum(len(e[3]) // 2 for e in graph["edges"])), flush=True)
    os.makedirs("track", exist_ok=True)
    with gzip.open("track/railgraph.json.gz", "wt", encoding="utf-8", compresslevel=9) as f:
        json.dump(graph, f, separators=(",", ":"))
    print("written track/railgraph.json.gz: %.1f MB" % (os.path.getsize("track/railgraph.json.gz") / 1e6))


if __name__ == "__main__":
    main()
