"""Shortest path along the real track between two stations, from track/railgraph.json.gz
(built by build_tracks.py from OpenStreetMap; (c) OpenStreetMap contributors, ODbL)."""
import gzip
import heapq
import json
import logging
import math
import os

GRAPH_PATH = os.getenv("RAILGRAPH", "track/railgraph.json.gz")
log = logging.getLogger("railfeed.tracks")


class Tracks:
    def __init__(self, path=GRAPH_PATH):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            g = json.load(f)
        self.nodes = g["nodes"]
        self.edges = g["edges"]
        self.stations = g["stations"]  # tiploc -> [[node, snap distance m], ...]
        self.adj = [[] for _ in self.nodes]
        for i, (u, v, length, _) in enumerate(self.edges):
            self.adj[u].append((v, length, i, True))
            self.adj[v].append((u, length, i, False))
        self.cache = {}

    def has(self, tiploc):
        return tiploc in self.stations

    def route(self, a, b):
        """-> [[lat, lon], ...] from station a to station b along the track, or None."""
        src, dst = self.stations.get(a), self.stations.get(b)
        if not src or not dst or a == b:
            return None
        dst = {n: d for n, d in dst}
        (la1, lo1), (la2, lo2) = self.nodes[src[0][0]], self.nodes[next(iter(dst))]
        straight = math.hypot(la2 - la1, (lo2 - lo1) * math.cos(math.radians(la1))) * 111320
        limit = straight * 3 + 3000  # a route far longer than the crow flies is probably wrong
        dist, prev = {}, {}
        heap = []
        for n, d in src:
            dist[n], prev[n] = d, None
            heap.append((d, n))
        heapq.heapify(heap)
        best, best_node = math.inf, None
        while heap:
            d, n = heapq.heappop(heap)
            if d > dist.get(n, math.inf):
                continue
            if d >= best or d > limit:
                break
            if n in dst and d + dst[n] < best:
                best, best_node = d + dst[n], n
            for m, w, ei, fwd in self.adj[n]:
                nd = d + w
                if nd < dist.get(m, math.inf):
                    dist[m] = nd
                    prev[m] = (n, ei, fwd)
                    heapq.heappush(heap, (nd, m))
        if best_node is None:
            return None
        legs, n = [], best_node
        while prev[n] is not None:
            p, ei, fwd = prev[n]
            legs.append((ei, fwd))
            n = p
        poly = []
        for ei, fwd in reversed(legs):
            flat = self.edges[ei][3]
            pts = [[flat[k], flat[k + 1]] for k in range(0, len(flat), 2)]
            if not fwd:
                pts.reverse()
            poly.extend(pts if not poly else pts[1:])
        return poly if len(poly) >= 2 else None

    def route_json(self, a, b):
        """Cached JSON text of the route (small: a few KB per station pair)."""
        key = (a, b)
        if key not in self.cache:
            self.cache[key] = json.dumps({"p": self.route(a, b)}, separators=(",", ":"))
        return self.cache[key]


def load():
    if not os.path.exists(GRAPH_PATH):
        log.warning("No track graph at %s: trains will move in straight lines", GRAPH_PATH)
        return None
    t = Tracks()
    log.info("Track graph loaded: %d nodes, %d edges, %d stations", len(t.nodes), len(t.edges), len(t.stations))
    return t
