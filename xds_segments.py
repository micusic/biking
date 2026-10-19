"""Segments: fixed stretches of road timed on every ride that passes them in the set direction.

Defined in rides/segments.json (WGS-84, as recorded by the bike computer):
  [{"id": "zhangba-east", "name": "丈八东路（无红绿灯段）", "start": [lat, lon], "end": [lat, lon]}]
Optional keys used by the page: note, targets [{label, power, text}], hr (heart-rate note), split (0–1: report
power before/after this fraction), hard_min_power (passes at or above it count as 强度日), metric (default chart),
norm_power (W to normalise end-of-segment HR to).
A pass = the track comes within 35 m of `start`, then within 35 m of `end`, having covered 0.85–1.3× the
straight-line length in under 15 minutes. Heart rate counts only when the ride's HR is trustworthy
(rides/hr_quality.json not suspect/bad) and present for ≥90% of the pass.
"""
import json
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SEGMENTS = os.path.join(HERE, "rides", "segments.json")
DEFAULT = [{
    "id": "zhangba-east",
    "name": "丈八东路（无红绿灯段）",
    "note": "上班方向，自东向西，约 940 m，平路",
    "start": [34.2001, 108.8758],
    "end": [34.2001, 108.8655],
}]
NEAR_M = 35


def m(a, b):
    return math.hypot((a[0] - b[0]) * 111195, (a[1] - b[1]) * 111195 * math.cos(math.radians(a[0])))


def load():
    if not os.path.exists(SEGMENTS):
        with open(SEGMENTS, "w", encoding="utf-8") as f:
            json.dump(DEFAULT, f, ensure_ascii=False, indent=1)
    with open(SEGMENTS, encoding="utf-8") as f:
        return json.load(f)


def _closest(pts, i0, target):
    """Index of the closest approach to target in the run of points within NEAR_M starting at i0."""
    best, bi, i = 1e9, None, i0
    while i < len(pts) and pts[i]["lat"] and m((pts[i]["lat"], pts[i]["lon"]), target) < NEAR_M * 2:
        d = m((pts[i]["lat"], pts[i]["lon"]), target)
        if d < best:
            best, bi = d, i
        i += 1
    return bi, i


def efforts(pts, seg, hr_ok):
    """All passes of one segment in one ride (pts: parsed points, bad HR already zeroed)."""
    s, e = tuple(seg["start"]), tuple(seg["end"])
    straight = m(s, e)
    out, i, n = [], 0, len(pts)
    while i < n:
        p = pts[i]
        if p["lat"] and m((p["lat"], p["lon"]), s) < NEAR_M:
            a, i = _closest(pts, i, s)
            j = i
            while j < n and pts[j]["ts"] - pts[a]["ts"] < 900:
                q = pts[j]
                if q["lat"] and m((q["lat"], q["lon"]), e) < NEAR_M:
                    b, _ = _closest(pts, j, e)
                    dist = pts[b]["dist"] - pts[a]["dist"]
                    secs = pts[b]["ts"] - pts[a]["ts"]
                    if 0.85 * straight <= dist <= 1.3 * straight and secs > 0:
                        seg_pts = pts[a:b + 1]
                        hrs = [x["hr"] for x in seg_pts if x["hr"]]
                        pw = [x["power"] for x in seg_pts]
                        hr_good = hr_ok and len(hrs) >= 0.9 * len(seg_pts)
                        end_hrs = [x["hr"] for x in seg_pts[-20:] if x["hr"]]
                        e = dict(
                            ts=pts[a]["ts"], secs=secs, dist=round(dist), spd=round(dist / secs * 3.6, 1),
                            pw=round(sum(pw) / len(pw)) if any(pw) else None,
                            cad=round(sum(x["cad"] for x in seg_pts) / len(seg_pts)),
                            hr=round(sum(hrs) / len(hrs)) if hr_good else None,
                            hr_end=round(sum(end_hrs) / len(end_hrs)) if hr_good and end_hrs else None,
                            km=[round(pts[a]["dist"] / 1000, 3), round(pts[b]["dist"] / 1000, 3)])
                        if seg.get("split") and any(pw):  # pacing check: power in the first vs second part
                            cut = pts[a]["dist"] + dist * seg["split"]
                            p1 = [x["power"] for x in seg_pts if x["dist"] < cut]
                            p2 = [x["power"] for x in seg_pts if x["dist"] >= cut]
                            e["pw1"], e["pw2"] = round(sum(p1) / len(p1)), round(sum(p2) / len(p2))
                        out.append(e)
                    i = b
                    break
                j += 1
        i += 1
    return out
