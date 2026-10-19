"""Convert XDS (喜德盛骑行) cloud ride data (/v1.1/cycling/{id}/bin, FlatBuffers) to FIT.

Usage: python xds_bin2fit.py <bins_dir> <rides_index.json> <out_dir>

Point table field map (verified against an app-exported FIT, 4255/4262 points identical):
  f0 int64 unix ts | f3 float cumulative distance m | f5 int speed km/h*10 | f6 int cadence
  f7 int heart rate | f8 float power W | f9 double altitude m | f10 double lat | f11 double lon
  f12 int gps accuracy | f13 float left power | f14 float right power | f15 float bearing
Missing fields are FlatBuffers defaults (0).
"""
import datetime
import json
import os
import struct
import sys
import zlib

from fit_tool.fit_file_builder import FitFileBuilder
from fit_tool.profile.messages.activity_message import ActivityMessage
from fit_tool.profile.messages.device_info_message import DeviceInfoMessage
from fit_tool.profile.messages.event_message import EventMessage
from fit_tool.profile.messages.file_id_message import FileIdMessage
from fit_tool.profile.messages.lap_message import LapMessage
from fit_tool.profile.messages.record_message import RecordMessage
from fit_tool.profile.messages.session_message import SessionMessage
from fit_tool.profile.profile_type import (Activity, Event, EventType, FileType, Manufacturer,
                                           Sport, SubSport)

TZ = datetime.timezone(datetime.timedelta(hours=8))


def parse_points(b):
    u32 = lambda o: struct.unpack_from("<I", b, o)[0]
    i32 = lambda o: struct.unpack_from("<i", b, o)[0]
    u16 = lambda o: struct.unpack_from("<H", b, o)[0]

    def tab(t):
        vt = t - i32(t)
        offs = [u16(vt + 4 + 2 * i) for i in range((u16(vt) - 4) // 2)]
        return {i: t + o for i, o in enumerate(offs) if o}

    def vec(p):
        v = p + u32(p)
        return [v + 4 + 4 * k + u32(v + 4 + 4 * k) for k in range(u32(v))]

    root = tab(u32(0))
    pts = []
    for lap in vec(root[22]):
        lf = tab(lap)
        if 21 not in lf:
            continue
        for t in vec(lf[21]):
            f = tab(t)
            g = lambda i, fmt, d=0: struct.unpack_from(fmt, b, f[i])[0] if i in f else d
            pts.append(dict(
                ts=g(0, "<q"), dist=g(3, "<f"), speed=g(5, "<i") / 36.0, cad=g(6, "<i"),
                hr=g(7, "<i"), power=g(8, "<f"), alt=g(9, "<d", None), lat=g(10, "<d"),
                lon=g(11, "<d"), lpow=g(13, "<f"), rpow=g(14, "<f")))
    pts.sort(key=lambda p: p["ts"])
    return pts


def build_fit(ride, pts):
    start_ms = ride["started_at"] * 1000
    end_ms = (pts[-1]["ts"] if pts else ride["finished_at"]) * 1000
    b = FitFileBuilder(auto_define=True, min_string_size=50)

    m = FileIdMessage()
    m.type = FileType.ACTIVITY
    m.manufacturer = Manufacturer.DEVELOPMENT.value
    m.product = 0
    m.time_created = start_ms
    m.serial_number = zlib.crc32(ride["id"].encode())
    b.add(m)

    m = DeviceInfoMessage()
    m.timestamp = start_ms
    m.manufacturer = Manufacturer.DEVELOPMENT.value
    m.product = 0
    b.add(m)

    m = EventMessage()
    m.event = Event.TIMER
    m.event_type = EventType.START
    m.timestamp = start_ms
    b.add(m)

    hrs, cads, spds = [], [], []
    for p in pts:
        r = RecordMessage()
        r.timestamp = p["ts"] * 1000
        if p["lat"] or p["lon"]:
            r.position_lat = p["lat"]
            r.position_long = p["lon"]
        if p["alt"] is not None:
            r.altitude = p["alt"]
        r.distance = p["dist"]
        r.speed = p["speed"]
        spds.append(p["speed"])
        if p["hr"]:
            r.heart_rate = p["hr"]
            hrs.append(p["hr"])
        r.cadence = p["cad"]
        if p["cad"]:
            cads.append(p["cad"])
        r.power = int(round(p["power"]))
        tot = p["lpow"] + p["rpow"]
        if tot > 0:
            r.left_right_balance = 0x80 | int(round(p["rpow"] / tot * 100))  # 0x80 = right side %
        b.add(r)

    m = EventMessage()
    m.event = Event.TIMER
    m.event_type = EventType.STOP_ALL
    m.timestamp = end_ms
    b.add(m)

    elapsed = (end_ms - start_ms) / 1000.0
    summary = dict(
        start_time=start_ms, timestamp=end_ms, total_elapsed_time=elapsed,
        total_timer_time=float(ride["seconds"]), total_distance=ride["distance"],
        total_calories=int(ride.get("calories") or 0),
        avg_speed=ride["avgSpeed"] / 36.0, max_speed=max(spds) if spds else 0,
        total_ascent=int(ride.get("gain_altitude") or 0),
        total_descent=int(ride.get("descent_altitude") or 0))
    if hrs:
        summary.update(avg_heart_rate=round(sum(hrs) / len(hrs)), max_heart_rate=max(hrs))
    if ride.get("average_cadence"):
        summary.update(avg_cadence=round(ride["average_cadence"]), max_cadence=int(ride["max_cadence"]))
    if ride.get("average_power"):
        summary.update(avg_power=round(ride["average_power"]), max_power=int(ride["max_power"]))

    lap = LapMessage()
    for k, v in summary.items():
        setattr(lap, k, v)
    lap.event = Event.LAP
    lap.event_type = EventType.STOP
    lap.sport = Sport.CYCLING
    b.add(lap)

    s = SessionMessage()
    for k, v in summary.items():
        setattr(s, k, v)
    s.event = Event.SESSION
    s.event_type = EventType.STOP
    s.sport = Sport.CYCLING
    s.sub_sport = SubSport.GENERIC
    s.first_lap_index = 0
    s.num_laps = 1
    b.add(s)

    a = ActivityMessage()
    a.timestamp = end_ms
    a.total_timer_time = float(ride["seconds"])
    a.num_sessions = 1
    a.type = Activity.MANUAL
    a.event = Event.ACTIVITY
    a.event_type = EventType.STOP
    a.local_timestamp = int(end_ms / 1000 + 8 * 3600 - 631065600)
    b.add(a)
    return b.build()


def fit_names(rides):
    """Deterministic file name per ride id; a ride sharing its name with another gets an id suffix."""
    base = {}
    for r in rides:
        st = datetime.datetime.fromtimestamp(r["started_at"], TZ)
        base[r["id"]] = f"{st:%Y-%m-%d_%H%M}_{r['distance'] / 1000:.1f}km"
    counts = {}
    for n in base.values():
        counts[n] = counts.get(n, 0) + 1
    return {i: (n if counts[n] == 1 else f"{n}_{i[:6]}") + ".fit" for i, n in base.items()}


def main(bins, index, out):
    os.makedirs(out, exist_ok=True)
    rides = json.load(open(index, encoding="utf-8"))
    names = fit_names(rides)
    for ride in rides:
        pts = parse_points(open(os.path.join(bins, ride["id"] + ".bin"), "rb").read())
        name = names[ride["id"]]
        build_fit(ride, pts).to_file(os.path.join(out, name))
        gps = sum(1 for p in pts if p["lat"])
        print(f"{name}\t{len(pts)} pts\t{gps} gps")


if __name__ == "__main__":
    main(*sys.argv[1:4])
