"""Home/work detection and place-based ride names.

- Home and work are the two most common ride endpoints; a ride is a commute when it starts within 2 km of one
  and ends within 2 km of the other (tolerates a bike computer whose GPS drifts at power-on).
- Other rides get a name from OpenStreetMap: named parks / sights / water along the route (Overpass) or the
  district at the turnaround point (Nominatim). Route points within 1 km of home or work are never sent out.
- Names are cached in rides/names.json ({id: name}); edit any entry by hand and it is kept as is.
  A name starting with 上班通勤 / 下班通勤 also marks the ride as a commute.
"""
import collections
import json
import math
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

_orig_getaddrinfo = socket.getaddrinfo


def _ipv4_first(host, *a, **kw):
    """IPv6 to the OSM servers times out here (~20 s per request before urllib falls back); prefer IPv4."""
    res = _orig_getaddrinfo(host, *a, **kw)
    v4 = [r for r in res if r[0] == socket.AF_INET]
    return v4 or res


socket.getaddrinfo = _ipv4_first

HERE = os.path.dirname(os.path.abspath(__file__))
NAMES = os.path.join(HERE, "rides", "names.json")
UA = {"User-Agent": "xds-ride-viewer/1.0 (personal ride log)"}
NEAR_ANCHOR_KM = 2.0
PRIVATE_KM = 1.0


def km(a, b):
    return math.hypot((a[0] - b[0]) * 111.2, (a[1] - b[1]) * 111.2 * math.cos(math.radians(a[0])))


def anchors(data):
    """Return (home, work) as [lat, lon], or (None, None). data: dashboard ride dicts with 'trk' and 'start'."""
    ends = [(r, r["trk"][0], r["trk"][-1]) for r in data if len(r["trk"]) > 1]
    cnt = collections.Counter((round(p[0] / 0.005), round(p[1] / 0.005)) for _, s, e in ends for p in (s, e))
    cells = [((c[0] * 0.005, c[1] * 0.005), n) for c, n in cnt.most_common()]
    if not cells:
        return None, None
    a = cells[0][0]
    b = next((c for c, _ in cells if km(c, a) > 5), None)
    if b is None:
        return None, None
    # whichever anchor the morning rides between the two leave from is home
    morning_from = collections.Counter()
    for r, s, e in ends:
        hour = time.localtime(r["start"]).tm_hour
        for x, y in ((a, b), (b, a)):
            if km(s, x) < NEAR_ANCHOR_KM and km(e, y) < NEAR_ANCHOR_KM and hour < 12:
                morning_from[x] += 1
    home = a if morning_from[a] >= morning_from[b] else b
    return list(home), list(b if home == a else a)


def is_commute(r, home, work):
    if not home or len(r["trk"]) < 2:
        return False
    s, e = r["trk"][0], r["trk"][-1]
    return ((km(s, home) < NEAR_ANCHOR_KM and km(e, work) < NEAR_ANCHOR_KM)
            or (km(s, work) < NEAR_ANCHOR_KM and km(e, home) < NEAR_ANCHOR_KM))


def _get(url, data=None, timeout=60, tries=4):
    for n in range(tries):  # the public Overpass server often answers 429/502/504 when busy
        try:
            req = urllib.request.Request(url, data=data, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code not in (429, 502, 503, 504) or n == tries - 1:
                raise
        except (urllib.error.URLError, TimeoutError):
            if n == tries - 1:
                raise
        time.sleep(10 * (n + 1))


TYPE_RANK = [("tourism", ("attraction", "museum", "theme_park", "zoo", "viewpoint")), ("historic", None),
             ("leisure", ("park", "nature_reserve", "garden")), ("natural", ("water", "wetland")),
             ("water", None), ("waterway", ("river", "canal"))]


def _rank(tags):
    for i, (k, vals) in enumerate(TYPE_RANK):
        if k in tags and (vals is None or tags[k] in vals):
            return i
    return len(TYPE_RANK)


PHRASES = ["attraction", "park", "lake"]  # Nominatim special phrases → tourism / leisure=park / water


def features_near(points, radius_km=0.8):
    """Named sights / parks / lakes within ~radius_km of a few points (list of [lat, lon]), via Nominatim search
    bounded to a small box around each point (the public Overpass server proved too slow)."""
    out, seen = [], set()
    for p in points:
        dlat = radius_km / 111.2
        dlon = radius_km / (111.2 * math.cos(math.radians(p[0])))
        box = f"{p[1] - dlon:.5f},{p[0] + dlat:.5f},{p[1] + dlon:.5f},{p[0] - dlat:.5f}"
        for ph in PHRASES:
            d = _get("https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
                dict(format="jsonv2", q=ph, viewbox=box, bounded=1, limit=10, **{"accept-language": "zh-CN"})))
            time.sleep(1.1)  # Nominatim usage policy: max 1 request per second
            for x in d:
                name = x.get("name")
                if name and name not in seen:
                    seen.add(name)
                    out.append(dict(name=name, rank=_rank({x.get("category"): x.get("type")}),
                                    ll=[float(x["lat"]), float(x["lon"])]))
    return out


def area_name(ll):
    d = _get("https://nominatim.openstreetmap.org/reverse?" + urllib.parse.urlencode(
        dict(format="jsonv2", lat=f"{ll[0]:.5f}", lon=f"{ll[1]:.5f}", zoom=15, **{"accept-language": "zh-CN"})))
    a = d.get("address", {})
    for k in ("neighbourhood", "quarter", "suburb", "village", "town", "city_district", "road"):
        if a.get(k):
            return a[k].replace("街道", "").replace("社区", "") or a[k]
    return d.get("name") or None


def _short(name):
    for suf in ("遗址公园", "国家湿地公园", "湿地公园", "森林公园", "生态公园", "休闲公园", "公园"):
        if name.endswith(suf) and len(name) - len(suf) >= 2 and suf != "公园":
            return name[: -len(suf)]
    return name


def name_ride(r, home, work):
    trk = r["trk"]
    if r["dist"] < 1:
        return "试骑一小段"
    s, e = trk[0], trk[-1]
    label = lambda p: "家" if home and km(p, home) < PRIVATE_KM else "单位" if work and km(p, work) < PRIVATE_KM else None
    public = [p for p in trk if not (home and km(p, home) < PRIVATE_KM) and not (work and km(p, work) < PRIVATE_KM)]
    if not public:
        return "家附近转转"
    far = max(public, key=lambda p: km(p, s))
    i = public.index(far)
    probes = [far, public[i // 2], e if not label(e) else public[(i + len(public)) // 2]]
    feats = features_near(probes)
    time.sleep(1)
    feats.sort(key=lambda f: (f["rank"], km(f["ll"], far)))
    top = _short(feats[0]["name"]) if feats else None
    if km(s, e) < 1.0:  # back where it started: out-and-back or loop
        back = trk[len(trk) // 2:]
        out = trk[: len(trk) // 2]
        retrace = sum(1 for p in back[::5] if min(km(p, q) for q in out[::3]) < 0.08) / max(1, len(back[::5]))
        place = top or area_name(far)
        time.sleep(1)
        if not place:
            return None
        return f"{place}往返" if retrace > 0.6 else f"绕{place}一圈"
    a = label(s) or (area_name(s) if not top else None)
    b = label(e)
    if not b:
        near_end = [f for f in feats if km(f["ll"], e) < 0.6]
        b = _short(near_end[0]["name"]) if near_end else area_name(e)
        time.sleep(1)
    if not a:
        a = area_name(s)
        time.sleep(1)
    if a and b and a != b:
        return f"{a} → {b}"
    return f"{top}骑行" if top else None


def load_names():
    try:
        with open(NAMES, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def set_name(ride_id, name):
    """Write one name into names.json (empty name = forget it). Re-reads the file first so a rename from the
    page and a background lookup never overwrite each other."""
    names = load_names()
    if name:
        names[ride_id] = name
    else:
        names.pop(ride_id, None)
    tmp = NAMES + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(names, f, ensure_ascii=False, indent=1)
    os.replace(tmp, NAMES)


def assign_names(data, log=print):
    """Set r['commute'] and r['name'] on every dashboard ride; looks up only rides missing from names.json."""
    home, work = anchors(data)
    names = load_names()
    for r in data:
        manual = names.get(r["id"])
        if manual and manual.startswith(("上班通勤", "下班通勤")):  # hand-marked commute, e.g. a detour
            r["commute"], r["name"] = True, manual
            continue
        r["commute"] = is_commute(r, home, work)
        if r["commute"]:
            r["name"] = manual  # usually None: the page names commutes 上班/下班 itself
            continue
        if r["id"] not in names and not os.environ.get("XDS_NO_LOOKUP"):
            try:
                n = name_ride(r, home, work)
            except Exception as e:  # offline or service busy: retry next build
                log(f"  地名查询失败 {r['id'][:8]}：{e}")
                n = None
            if n:
                names[r["id"]] = n
                set_name(r["id"], n)  # save as we go; lookups are slow
                log(f"  命名 {time.strftime('%m-%d', time.localtime(r['start']))} {r['dist']} km → {n}")
        r["name"] = names.get(r["id"])
