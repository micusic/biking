@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem 先拉取云端点评 Routine 推上来的新骑行和点评，再在本机生成页面
git pull --ff-only --quiet || echo 拉取最新数据失败，先用本地数据打开
set XDS_NO_LOOKUP=1
uv run --with fit-tool python xds_dashboard.py >nul
set XDS_NO_LOOKUP=
uv run xds_app.py
