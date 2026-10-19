"""Hourly weather for each ride (the ride data has no temperature).

Source: Open-Meteo (free, no key), hourly temperature / feels-like / humidity / wind for central Xi'an —
only the city-centre point is sent, never the ride track. Cached in rides/weather.json
({"YYYY-MM-DDTHH:00": [°C, feels-like °C, humidity %, wind km/h]}, Beijing time), so each hour is fetched once.
Network: archive-api.open-meteo.com (older than ~5 days) and api.open-meteo.com (recent days).
"""
import datetime
import json
import os
import statistics as st
import urllib.parse

import xds_places  # noqa: F401  (forces IPv4 and provides _get with retries)
from xds_bin2fit import TZ

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "rides", "weather.json")
LAT, LON = 34.26, 108.94          # central Xi'an
VARS = "temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m"
TEMP_HR_PER_10C = 3.4             # bpm higher at the same power per +10 °C (regression on 61 commutes, ±1.3)


def _load():
    try:
        with open(CACHE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _fetch(url, params):
    d = xds_places._get(url + "?" + urllib.parse.urlencode(params), timeout=60)
    h = d.get("hourly", {})
    out = {}
    for i, t in enumerate(h.get("time", [])):
        row = [h[k][i] for k in VARS.split(",")]
        if row[0] is not None:
            out[t] = [round(v, 1) for v in row]
    return out


def ensure(dates, log=print):
    """Make sure the cache has every hour of these dates (datetime.date); fetch what is missing."""
    cache = _load()
    today = datetime.datetime.now(TZ).date()
    # the last two days are re-fetched: earlier fetches may have held forecast rather than observed values
    missing = sorted({d for d in dates if f"{d:%Y-%m-%d}T12:00" not in cache or (today - d).days <= 1})
    if not missing or os.environ.get("XDS_NO_LOOKUP"):
        return cache
    old = [d for d in missing if (today - d).days > 5]
    new = [d for d in missing if (today - d).days <= 5]
    try:
        if old:
            cache.update(_fetch("https://archive-api.open-meteo.com/v1/archive", dict(
                latitude=LAT, longitude=LON, hourly=VARS, timezone="Asia/Shanghai",
                start_date=f"{min(old):%Y-%m-%d}", end_date=f"{max(old):%Y-%m-%d}")))
        if new:
            cache.update(_fetch("https://api.open-meteo.com/v1/forecast", dict(
                latitude=LAT, longitude=LON, hourly=VARS, timezone="Asia/Shanghai",
                past_days=min(92, (today - min(new)).days + 1), forecast_days=1)))
        tmp = CACHE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(dict(sorted(cache.items())), f, separators=(",", ":"))
        os.replace(tmp, CACHE)
    except Exception as e:  # offline: rides just show no weather this time
        log(f"  天气获取失败：{e}")
    return cache


def for_ride(cache, start, end):
    """Average weather over the ride, or None."""
    rows, t = [], start
    while t <= end:
        k = datetime.datetime.fromtimestamp(t, TZ).strftime("%Y-%m-%dT%H:00")
        if k in cache:
            rows.append(cache[k])
        t += 900
    if not rows:
        return None
    temp, feels, rh, wind = (round(st.mean(r[i] for r in rows), 1) for i in range(4))
    return dict(temp=temp, feels=feels, rh=round(rh), wind=round(wind))
