@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem 先拉取云端点评 Routine 推上来的新骑行、点评和体重，再在本机生成页面
rem body_data.csv / cloud_*.csv 由云端重新生成，本机的改动会挡住拉取，先还原
git checkout -- picooc/body_data.csv picooc/cloud_825404.csv 2>nul
git pull --ff-only --quiet || echo 拉取最新数据失败，先用本地数据打开
rem 电脑蓝牙称的体重（weigh.bat）只在本机 weights.csv 里：推上去，云端点评才看得到
git diff --quiet -- picooc/weights.csv || (git commit -q -m "体重：蓝牙称重" -- picooc/weights.csv && git push -q origin HEAD:main || echo 蓝牙称重推送失败，下次再试)
set XDS_NO_LOOKUP=1
uv run --with fit-tool python xds_dashboard.py >nul
set XDS_NO_LOOKUP=
uv run xds_app.py
