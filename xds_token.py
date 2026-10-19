# /// script
# requires-python = ">=3.10"
# ///
"""Copy the XDS login from the latest iPhone backup, for the cloud environment's variables.

Usage: uv run xds_token.py          → shows XDS_UID, copies XDS_TOKEN to the clipboard (never printed)
       uv run xds_token.py uid      → copies XDS_UID instead
Then paste into the cloud environment (claude.ai → Code → Environments → Personal → environment variables).
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from xds_sync import latest_backup, read_login  # noqa: E402


def to_clipboard(text):
    subprocess.run(["clip"], input=text.encode("utf-16-le"), check=True)  # Windows clipboard


if __name__ == "__main__":
    if not sys.stdout.isatty():
        sys.stdout.reconfigure(encoding="utf-8")
    uid, token = read_login(latest_backup())
    if len(sys.argv) > 1 and sys.argv[1] == "uid":
        to_clipboard(uid)
        print(f"已复制 XDS_UID 到剪贴板：{uid}")
    else:
        to_clipboard(token)
        print(f"XDS_UID = {uid}")
        print(f"已把 XDS_TOKEN（{len(token)} 位）复制到剪贴板，直接粘贴到云端环境变量里即可。")
