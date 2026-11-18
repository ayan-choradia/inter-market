@echo off
title Intermarket Curves Intelligence Platform
echo =======================================================================
echo Starting Standalone Intermarket Curves Intelligence Platform
echo Web Dashboard URL: http://localhost:8050/
echo =======================================================================
start http://localhost:8050/
python -m uvicorn app:app --host 0.0.0.0 --port 8050 --reload
pause
