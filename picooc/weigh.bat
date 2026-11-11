@echo off
cd /d "%~dp0"
echo Step on the scale (barefoot). Waiting up to 5 minutes...
picooc.exe 6472D8B55D67 male 178 39 300 weights.csv
pause
