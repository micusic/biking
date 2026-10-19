# /// script
# requires-python = ">=3.10"
# dependencies = ["fit-tool"]
# ///
"""Local viewer for the ride dashboard, with renames saved to rides/names.json.

Usage: uv run xds_app.py            (or double-click 骑行记录.cmd) → opens http://127.0.0.1:8770/
Listens on 127.0.0.1 only, so nothing outside this computer can reach it.

  GET  /                  rides/dashboard.html
  GET  /api/names         names.json, so a reload shows renames made since the page was built
  POST /api/name          {"id": ..., "name": ...}; an empty name forgets it (auto / 上下班 name again)
"""
import json
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from xds_places import load_names, set_name  # noqa: E402

PAGE = os.path.join(HERE, "rides", "dashboard.html")
INDEX = os.path.join(HERE, "rides", "rides_index.json")
PORT = int(os.environ.get("XDS_PORT", "8770"))
MAX_NAME = 40


def ride_ids():
    with open(INDEX, encoding="utf-8") as f:
        return {r["id"] for r in json.load(f)}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/dashboard.html"):
            if not os.path.exists(PAGE):
                return self._send(404, "还没有生成 dashboard.html，先运行 uv run xds_sync.py".encode(), "text/plain; charset=utf-8")
            with open(PAGE, "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if path == "/api/names":
            return self._send(200, load_names())
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/api/name":
            return self._send(404, {"error": "not found"})
        # same-origin only: the page itself, not some other site in the browser
        origin = self.headers.get("Origin")
        if origin and origin not in (f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"):
            return self._send(403, {"error": "forbidden"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0))
            rid, name = str(body["id"]), str(body.get("name") or "").strip()
        except (ValueError, KeyError, TypeError):
            return self._send(400, {"error": "bad request"})
        if rid not in ride_ids():
            return self._send(404, {"error": "unknown ride"})
        if len(name) > MAX_NAME:
            return self._send(400, {"error": f"名字最多 {MAX_NAME} 个字"})
        set_name(rid, name)
        self._send(200, {"ok": True, "id": rid, "name": name or None})

    def log_message(self, fmt, *args):
        if self.command == "POST":
            sys.stdout.write("  " + (fmt % args) + "\n")


def main():
    url = f"http://127.0.0.1:{PORT}/"
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"骑行记录：{url}  （关闭这个窗口即退出）")
    if not os.environ.get("XDS_NO_BROWSER"):
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
