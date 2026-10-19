# /// script
# requires-python = ">=3.10"
# dependencies = ["fit-tool"]
# ///
"""Coach + doctor reviews of new rides (used by the scheduled routine "骑行点评").

  uv run xds_review.py pending [--no-sync]
      Sync from the cloud (unless --no-sync), then print a JSON packet of every ride that has no review yet:
      the ride's numbers, how it compares with recent rides in the same direction, both segments against their
      targets, heart-rate data quality and training load. Also written to rides/reviews/pending.json.
  uv run xds_review.py save <file.json>
      Store reviews ({"reviews": [{"id", "overall", "climb", "flat"}]}) as rides/reviews/<date>_<id>.json,
      append ride data + review to rides/reviews/journal.md, and rebuild the dashboard (shown on the ride page).

Only rides after rides/reviews/state.json "since" are reviewed; on first use that is the start of the latest
riding day, so history is not reviewed retroactively.
"""
import datetime
import json
import os
import statistics as st
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from xds_bin2fit import TZ  # noqa: E402
from xds_dashboard import REVIEWS, build, collect  # noqa: E402
from xds_sync import INDEX, RAW, apply_corrections  # noqa: E402
from xds_weather import TEMP_HR_PER_10C  # noqa: E402

STATE = os.path.join(REVIEWS, "state.json")
JOURNAL = os.path.join(REVIEWS, "journal.md")
HR_PER_W = 0.27  # bpm per watt between rides (regression on ride HR vs power and training load)
WD = "一二三四五六日"
PROFILE = dict(
    age=datetime.date.today().year - 1987, weight_kg=73, height_cm=178, hr_max_est=181, hr_max_seen=176,
    goals=["心肺功能提高", "肌肉力量提高"],
    temperature_note=("原始骑行数据没有温度，weather 来自公开天气数据。气温低时同功率心率会自然偏低，"
                      "评价心肺时先扣掉 weather.expected_same_power_hr_shift_from_temperature 再和基线比。"),
    context=("主要是上下班通勤：上班约 56 分钟（9:30 前到）、下班约 60 分钟（18:00 前到），时间固定，不能刻意放慢；"
             "下班爬升约是上班的两倍；路况复杂，不在通勤中做全力冲刺测试。能控制的只有两段：出门爬坡和丈八东路无灯段，"
             "两段都按功率骑、心率只看上限。体能跟训练量走：7–8 月天热停骑，各项指标明显下降，9 月恢复。"),
    hr_rule="hr_quality.status 为 suspect/bad 的骑行，心率数据不可信，不要据此评价心肺或给出心率相关结论。",
)


def fmt_t(s):
    return f"{int(s) // 60}:{int(s) % 60:02d}"


def day_str(ts):
    d = datetime.datetime.fromtimestamp(ts, TZ)
    return f"{d:%Y-%m-%d %H:%M} 周{WD[d.weekday()]}"


def title(r):
    if r.get("name"):
        return r["name"]
    h = datetime.datetime.fromtimestamp(r["start"], TZ).hour
    if r.get("commute"):
        return "上班通勤" if h < 12 else "下班通勤"
    return "骑行"


def load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def norm_hr(r):
    f = r.get("fit") or {}
    if f.get("ahr") is None or f.get("apw") is None:
        return None
    return round(f["ahr"] - HR_PER_W * (f["apw"] - 100), 1)


def training(data, upto):
    """Training load (42-day EWMA of daily kJ) on the ride's day, its peak so far, and recent frequency."""
    daily = {}
    for r in data:
        if r["start"] <= upto and r.get("fit") and r["fit"].get("kj"):
            k = datetime.datetime.fromtimestamp(r["start"], TZ).date()
            daily[k] = daily.get(k, 0) + r["fit"]["kj"]
    if not daily:
        return {}
    d, end = min(daily), datetime.datetime.fromtimestamp(upto, TZ).date()
    ctl, peak, peak_d = 0.0, 0.0, d
    while d <= end:
        ctl += (daily.get(d, 0) - ctl) / 42
        if ctl > peak:
            peak, peak_d = ctl, d
        d += datetime.timedelta(days=1)
    before = [r for r in data if r["start"] < upto]
    prev = max((r["start"] for r in before), default=None)
    days = lambda n: len({datetime.datetime.fromtimestamp(r["start"], TZ).date() for r in data
                          if upto - n * 86400 < r["start"] <= upto})
    return dict(load_now=round(ctl), load_peak=round(peak), load_peak_date=str(peak_d),
                load_pct_of_peak=round(100 * ctl / peak) if peak else None,
                days_since_previous_ride=round((upto - prev) / 86400, 1) if prev else None,
                riding_days_last_7=days(7), riding_days_last_14=days(14))


def weather_block(wx, base_temp):
    """Ride weather plus how much of a heart-rate difference the temperature alone would explain."""
    if not wx:
        return None
    out = dict(wx, source="Open-Meteo 西安市区逐小时（骑行时段平均）")
    if base_temp is not None:
        out["baseline_temperature"] = base_temp
        out["expected_same_power_hr_shift_from_temperature"] = round(TEMP_HR_PER_10C * (wx["temp"] - base_temp) / 10, 1)
    out["rule"] = (f"同样功率和训练量下，气温每高 10°C 心率约高 {TEMP_HR_PER_10C} bpm（±1.3）；"
                   "气温对红灯心率恢复、路段用时、骑行功率没有可测出的影响")
    return out


def packet_for(r, data, segdefs, reviews):
    am = datetime.datetime.fromtimestamp(r["start"], TZ).hour < 12
    f = r.get("fit") or {}
    same = [x for x in data if x.get("commute") and x["start"] < r["start"] and x["dist"] > 14
            and (datetime.datetime.fromtimestamp(x["start"], TZ).hour < 12) == am][-8:]
    med = lambda xs: round(st.median(xs), 1) if xs else None
    base = dict(n=len(same),
                speed=med([x["fit"]["mspd"] for x in same if x.get("fit") and x["fit"].get("mspd")]),
                power=med([x["fit"]["mpw"] for x in same if x.get("fit") and x["fit"].get("mpw") is not None]),
                same_power_hr=med([v for v in (norm_hr(x) for x in same) if v is not None]),
                red_light_hr_recovery=med([x["fit"]["hrr"] for x in same if x.get("fit") and x["fit"].get("hrr") is not None]),
                temperature=med([x["wx"]["temp"] for x in same if x.get("wx")]))
    segs = []
    for sd in segdefs:
        passes = sorted([dict(e, start=x["start"]) for x in data for e in x.get("segs", []) if e["sid"] == sd["id"]],
                        key=lambda e: e["ts"])
        mine = [e for e in passes if e["start"] == r["start"]]
        item = dict(id=sd["id"], name=sd["name"], note=sd.get("note"), targets=sd.get("targets"), hr_note=sd.get("hr"),
                    hard_min_power=sd.get("hard_min_power"), passed=bool(mine), total_passes=len(passes))
        if sd.get("hard_min_power"):
            hard = [p for p in passes if p.get("pw") and p["pw"] >= sd["hard_min_power"] and p["start"] <= r["start"]]
            item["last_hard_day"] = day_str(hard[-1]["start"])[:10] if hard else None
        if mine:
            e = mine[0]
            earlier = [p for p in passes if p["ts"] < e["ts"]]
            ranked = sorted(passes, key=lambda p: (p["secs"], p["ts"]))
            hr_norm = (round(e["hr_end"] - HR_PER_W * (e["pw"] - sd.get("norm_power", 110)), 1)
                       if e.get("hr_end") is not None and e.get("pw") is not None else None)
            recent = earlier[-5:]
            item.update(
                time=fmt_t(e["secs"]), seconds=e["secs"], rank=ranked.index(e) + 1,
                personal_record=not earlier or e["secs"] < min(p["secs"] for p in earlier),
                speed_kmh=e["spd"], power=e.get("pw"), first_half_power=e.get("pw1"), second_half_power=e.get("pw2"),
                cadence=e.get("cad"), hr_avg=e.get("hr"), hr_end=e.get("hr_end"),
                same_power_end_hr=hr_norm,
                hard_day=bool(sd.get("hard_min_power") and e.get("pw") and e["pw"] >= sd["hard_min_power"]),
                recent_5_median=dict(
                    time=fmt_t(st.median(p["secs"] for p in recent)) if recent else None,
                    power=med([p["pw"] for p in recent if p.get("pw") is not None]),
                    cadence=med([p["cad"] for p in recent]),
                    hr_end=med([p["hr_end"] for p in recent if p.get("hr_end") is not None])),
                best_time=fmt_t(ranked[0]["secs"]))
        segs.append(item)
    prev_reviews = sorted((v for v in reviews.values() if v.get("start", 0) < r["start"]), key=lambda v: v["start"])[-2:]
    return dict(
        id=r["id"], when=day_str(r["start"]), title=title(r), direction="上班" if am else "下班",
        commute=bool(r.get("commute")),
        ride=dict(distance_km=r["dist"], moving_time=fmt_t(r["secs"]), avg_speed_kmh=f.get("mspd") or r["avg"],
                  max_speed_kmh=r["max"], avg_power=f.get("mpw") if f.get("mpw") is not None else r.get("pw"),
                  max_power=r.get("maxpw"), avg_hr=r.get("hr"), max_hr=r.get("maxhr"), cadence=r.get("cad"),
                  climb_m=r.get("gain"), kcal=r.get("kcal"), best_1min_power=f.get("b1"), best_5min_power=f.get("b5"),
                  same_power_hr_at_100w=norm_hr(r), red_light_hr_recovery=f.get("hrr"),
                  red_light_stops_measured=f.get("hrr_n")),
        hr_quality=(r.get("hrq") or {"status": "ok"}),
        weather=weather_block(r.get("wx"), base["temperature"]),
        baseline_last_8_same_direction=base,
        segments=segs,
        training=training(data, r["start"]),
        previous_reviews=[dict(when=v["when"], overall=v["overall"][:160]) for v in prev_reviews],
    )


def pending(sync=True):
    if sync:
        import xds_sync
        xds_sync.main()
    os.makedirs(REVIEWS, exist_ok=True)
    with open(INDEX, encoding="utf-8") as f:
        rides, fixes = apply_corrections(json.load(f))
    data, segdefs = collect(rides, fixes, RAW)
    from xds_dashboard import load_reviews
    reviews = load_reviews()
    state = load_state()
    if "since" not in state:  # first use: start with the latest riding day
        last = datetime.datetime.fromtimestamp(data[-1]["start"], TZ)
        state["since"] = int(datetime.datetime(last.year, last.month, last.day, tzinfo=TZ).timestamp())
        with open(STATE, "w", encoding="utf-8") as f:
            json.dump(state, f)
    todo = [r for r in data if r["start"] >= state["since"] and r["id"] not in reviews and r["dist"] >= 1]
    out = dict(profile=PROFILE, rides=[packet_for(r, data, segdefs, reviews) for r in todo])
    with open(os.path.join(REVIEWS, "pending.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"\n# {len(todo)} ride(s) to review", file=sys.stderr)


def journal_entry(p, rv):
    rd = p["ride"]
    q = p["hr_quality"]
    hrq = "" if q.get("status") in (None, "ok", "minor") else f"（⚠ 心率存疑：{'、'.join(q.get('why', []))}）"
    lines = [f"## {p['when']} · {p['title']}", "",
             f"- 数据：{rd['distance_km']} km · {rd['moving_time']} · {rd['avg_speed_kmh']} km/h · "
             f"{rd['avg_power']} W · 心率 {rd['avg_hr'] or '–'} / {rd['max_hr'] or '–'}{hrq} · 踏频 {rd['cadence'] or '–'}"]
    for s in p["segments"]:
        if s["passed"]:
            split = f"（前 {s['first_half_power']} / 后 {s['second_half_power']} W）" if s.get("first_half_power") is not None else ""
            lines.append(f"- {s['name']}：{s['time']} · {s['power']} W{split} · 踏频 {s['cadence']} · "
                         f"结束心率 {s['hr_end'] or '–'} · 第 {s['rank']}/{s['total_passes']}"
                         f"{' · 强度日' if s.get('hard_day') else ''}{' · 个人纪录' if s.get('personal_record') else ''}")
    w = p.get("weather")
    if w:
        lines.append(f"- 天气：{w['temp']}°C（体感 {w['feels']}°C）· 湿度 {w['rh']}% · 风 {w['wind']} km/h")
    t = p["training"]
    if t:
        lines.append(f"- 训练量：{t['load_now']}（最高的 {t['load_pct_of_peak']}%），近 7 天骑 {t['riding_days_last_7']} 天")
    lines += ["", f"**整体**：{rv['overall']}", "", f"**爬坡路段**：{rv['climb']}", "", f"**无灯路段**：{rv['flat']}", "", ""]
    return "\n".join(lines)


def save(path):
    with open(path, encoding="utf-8") as f:
        body = json.load(f)
    items = body["reviews"] if isinstance(body, dict) and "reviews" in body else body if isinstance(body, list) else [body]
    with open(os.path.join(REVIEWS, "pending.json"), encoding="utf-8") as f:
        packets = {p["id"]: p for p in json.load(f)["rides"]}
    saved = []
    items = sorted(items, key=lambda rv: packets[rv['id']]['when'] if rv.get('id') in packets else '')  # journal stays in time order
    for rv in items:
        p = packets.get(rv.get("id"))
        if not p:
            print(f"跳过：{rv.get('id')} 不在 pending.json 里", file=sys.stderr)
            continue
        missing = [k for k in ("overall", "climb", "flat") if not str(rv.get(k, "")).strip()]
        if missing:
            raise SystemExit(f"{p['id']} 缺少：{missing}")
        start = int(datetime.datetime.strptime(p["when"][:16], "%Y-%m-%d %H:%M").replace(tzinfo=TZ).timestamp())
        rec = dict(id=p["id"], when=p["when"], title=p["title"], start=start,
                   written=datetime.datetime.now(TZ).strftime("%Y-%m-%d %H:%M"),
                   overall=rv["overall"].strip(), climb=rv["climb"].strip(), flat=rv["flat"].strip(), data=p)
        fn = os.path.join(REVIEWS, f"{p['when'][:10]}_{p['when'][11:13]}{p['when'][14:16]}_{p['id'][:8]}.json")
        with open(fn, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, indent=1)
        if not os.path.exists(JOURNAL):
            with open(JOURNAL, "w", encoding="utf-8") as f:
                f.write("# 骑行点评日志\n\n骑行教练 + 健康医生视角，每次骑行一条，新的在下面。\n\n")
        with open(JOURNAL, "a", encoding="utf-8") as f:
            f.write(journal_entry(p, rec))
        saved.append(p["when"] + " " + p["title"])
    with open(INDEX, encoding="utf-8") as f:
        rides, fixes = apply_corrections(json.load(f))
    build(rides, fixes, RAW)
    sys.stdout.reconfigure(encoding="utf-8")
    print(f"已保存 {len(saved)} 条点评：" + "；".join(saved))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "pending"
    if cmd == "pending":
        pending(sync="--no-sync" not in sys.argv)
    elif cmd == "save" and len(sys.argv) > 2:
        save(sys.argv[2])
    else:
        raise SystemExit(__doc__)
