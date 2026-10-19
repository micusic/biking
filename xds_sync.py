# /// script
# requires-python = ">=3.10"
# dependencies = ["fit-tool"]
# ///
"""Sync all XDS (喜德盛骑行) rides from the cloud to FIT files, using the login from the latest iPhone backup.

Usage: make an (unencrypted) iPhone backup with iTunes / Apple Devices, then:  uv run xds_sync.py

  rides/raw/<id>.bin        raw cloud data, kept as the original
  rides/all/*.fit           one FIT per ride
  rides/rides_index.json    ride list from the cloud (summary fields used for the FIT session)

Only rides not yet in rides/all are downloaded. The login token is read into memory only, never saved.
"""
import base64
import datetime
import json
import os
import plistlib
import sqlite3
import sys
import time
import urllib.error
import urllib.request

from xds_bin2fit import TZ, build_fit, fit_names, parse_points
from xds_hrq import clean as clean_hr, load_config as load_hrq_config, save_report as save_hrq_report

BACKUP_ROOTS = [os.path.expandvars(r"%USERPROFILE%\Apple\MobileSync\Backup"),
                os.path.expandvars(r"%APPDATA%\Apple Computer\MobileSync\Backup")]
API = "http://prdapi.xdsiot.com"
HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "rides", "raw")
OUT = os.path.join(HERE, "rides", "all")
INDEX = os.path.join(HERE, "rides", "rides_index.json")
CORRECTIONS = os.path.join(HERE, "rides", "corrections.json")
EXPIRED = ("登录凭证失效了（XDS_LOGIN_EXPIRED）。在手机上打开一次喜德盛骑行 App，再重新备份；"
           "云端运行时，备份后在本机运行 uv run xds_token.py，把新 token 更新到云端环境变量 XDS_TOKEN。")


def die(msg):
    print("✗ " + msg)
    sys.exit(1)


def latest_backup():
    dirs = [os.path.join(r, d) for r in BACKUP_ROOTS if os.path.isdir(r) for d in os.listdir(r)]
    dirs = [d for d in dirs if os.path.exists(os.path.join(d, "Manifest.db"))]
    if not dirs:
        die("没找到 iPhone 备份。先用 iTunes 或「Apple 设备」App 备份一次手机。")
    d = max(dirs, key=lambda d: os.path.getmtime(os.path.join(d, "Manifest.db")))
    with open(os.path.join(d, "Manifest.plist"), "rb") as f:
        if plistlib.load(f).get("IsEncrypted"):
            die("最新备份是加密的，读不了。备份时取消勾选「给本地备份加密」后再备份一次。")
    return d


def read_login(backup):
    db = sqlite3.connect(f"file:{os.path.join(backup, 'Manifest.db')}?mode=ro", uri=True)
    row = db.execute("select fileID from Files where domain='AppDomain-com.xds.ebike' "
                     "and relativePath='Library/Preferences/com.xds.ebike.plist'").fetchone()
    if not row:
        die("备份里没有喜德盛骑行 App 的数据。")
    with open(os.path.join(backup, row[0][:2], row[0]), "rb") as f:
        prefs = plistlib.load(f)
    if "UserModel" not in prefs:
        die("备份里 App 没登录。在手机上登录喜德盛骑行后再备份。")
    arc = plistlib.loads(prefs["UserModel"])
    objs = arc["$objects"]
    user = objs[arc["$top"]["root"].data]
    get = lambda k: objs[user[k].data] if isinstance(user.get(k), plistlib.UID) else user.get(k)
    uid, token = get("_id"), get("access_token")
    if not uid or not token:
        die("备份里没找到登录凭证。")
    return uid, token


class Api:
    def __init__(self, uid, token):
        auth = base64.b64encode(f"{uid}:{token}".encode()).decode()
        self.headers = {"Authorization": "Basic " + auth, "Accept": "*/*",
                        "User-Agent": "XDSRide/2.1.2 CFNetwork Darwin", "Accept-Language": "zh-Hans-CN;q=1"}

    def raw(self, path, tries=3):
        for n in range(tries):
            try:
                req = urllib.request.Request(API + path, headers=self.headers)
                with urllib.request.urlopen(req, timeout=60) as r:
                    return r.read()
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    die(EXPIRED)
                if n == tries - 1:
                    raise
            except urllib.error.URLError:
                if n == tries - 1:
                    raise
            time.sleep(2)

    def json(self, path):
        d = json.loads(self.raw(path))
        if not d.get("success"):
            msg = d.get("message", "")
            if "登录" in msg or "token" in msg.lower() or "授权" in msg:
                die(EXPIRED)
            die(f"接口返回失败：{path} {msg}")
        return d["retval"]


def apply_corrections(rides):
    """rides/corrections.json: {ride_id: {"shift_seconds": n, "note": "..."}} for rides whose bike-computer
    clock was wrong. Returns corrected copies of the rides and {id: shift}."""
    if not os.path.exists(CORRECTIONS):
        return rides, {}
    with open(CORRECTIONS, encoding="utf-8") as f:
        fixes = {i: int(c["shift_seconds"]) for i, c in json.load(f).items()}
    out = []
    for r in rides:
        if r["id"] in fixes:
            r = dict(r, started_at=r["started_at"] + fixes[r["id"]], finished_at=r["finished_at"] + fixes[r["id"]])
        out.append(r)
    return out, fixes


def main():
    if not sys.stdout.isatty():  # piped / Git Bash: Python would otherwise write GBK
        sys.stdout.reconfigure(encoding="utf-8")
    if os.environ.get("XDS_UID") and os.environ.get("XDS_TOKEN"):
        # cloud runs (no iPhone backup there): the login comes from the environment's variables
        print("使用环境变量里的登录凭证")
        api = Api(os.environ["XDS_UID"], os.environ["XDS_TOKEN"])
    else:
        backup = latest_backup()
        mtime = datetime.datetime.fromtimestamp(os.path.getmtime(os.path.join(backup, "Manifest.db")))
        print(f"使用备份：{os.path.basename(backup)}（{mtime:%Y-%m-%d %H:%M}）")
        api = Api(*read_login(backup))

    rides = []
    for y in api.json("/v4/cycling"):
        for m in api.json(f"/v4/cycling/{y['year']}"):
            rides += api.json(f"/v1.1/cycling/{m['year']}/{m['month']}")
            time.sleep(0.2)
    rides.sort(key=lambda r: r["started_at"], reverse=True)
    cloud = rides  # rides_index.json keeps the cloud's own values; corrections apply to the FITs only
    print(f"云端共 {len(rides)} 次骑行")

    os.makedirs(RAW, exist_ok=True)
    os.makedirs(OUT, exist_ok=True)
    cloud_names = fit_names(rides)
    rides, fixes = apply_corrections(rides)
    names = fit_names(rides)
    for i in fixes:  # drop the file written under the cloud's wrong time
        stale = os.path.join(OUT, cloud_names[i])
        if cloud_names[i] != names[i] and os.path.exists(stale):
            os.remove(stale)
    hrq = load_hrq_config()
    report = {}
    done, failed, recleaned = [], [], 0
    for r in rides:
        fit_path = os.path.join(OUT, names[r["id"]])
        is_new = not os.path.exists(fit_path)
        try:
            binf = os.path.join(RAW, r["id"] + ".bin")
            if not (os.path.exists(binf) and os.path.getsize(binf) > 0):
                data = api.raw(f"/v1.1/cycling/{r['id']}/bin")
                with open(binf + ".part", "wb") as f:
                    f.write(data)
                os.replace(binf + ".part", binf)
                time.sleep(0.2)
            with open(binf, "rb") as f:
                pts = parse_points(f.read())
            shift = fixes.get(r["id"], 0)
            for p in pts:
                p["ts"] += shift
            # drop heart-rate samples the strap got wrong (see xds_hrq.py / rides/hr_quality.json)
            info = clean_hr(pts, hrq["ceiling"], hrq["manual"].get(r["id"]))
            report[r["id"]] = info
            # FITs of rides with bad HR are rewritten every run, so rule changes and manual marks always apply
            if is_new or info.get("segments_km") or info["status"] == "bad":
                build_fit(r, pts).to_file(fit_path)
                if is_new:
                    done.append(r)
                else:
                    recleaned += 1
        except Exception as e:  # keep going; the next run retries whatever failed
            failed.append((r, e))
    save_hrq_report(report)

    with open(INDEX, "w", encoding="utf-8") as f:
        json.dump(cloud, f, ensure_ascii=False, indent=1)

    print(f"\n新增 {len(done)} 次：")
    for r in sorted(done, key=lambda r: r["started_at"]):
        st = datetime.datetime.fromtimestamp(r["started_at"], TZ)
        print(f"  {st:%Y-%m-%d %H:%M}  {r['distance'] / 1000:6.1f} km  {r['seconds'] // 60:3d} 分钟  → {names[r['id']]}")
    for r, e in failed:
        print(f"  ✗ 失败 {r['id']}: {e}（下次运行会重试）")
    suspect = [r for r in rides if report.get(r["id"], {}).get("status") in ("suspect", "bad")]
    if suspect:
        print(f"\n心率数据存疑 {len(suspect)} 次（异常点已从 FIT 和展示页去掉，详见 rides/hr_quality.json）：")
        for r in sorted(suspect, key=lambda r: r["started_at"]):
            q = report[r["id"]]
            st = datetime.datetime.fromtimestamp(r["started_at"], TZ)
            print(f"  {st:%Y-%m-%d %H:%M}  去掉 {q['bad_pct']}%  {'、'.join(q['reasons'])}")
    total = sum(r["distance"] for r in rides) / 1000
    print(f"\n累计 {len(rides)} 次，{total:.1f} km。FIT 文件在 {OUT}")
    from xds_dashboard import build
    print(f"展示页已更新：{build(rides, fixes, RAW)}")


if __name__ == "__main__":
    main()
