@echo off
title Trading Alerts System

echo ===================================================
echo Starting Trading Alerts Backend (FastAPI)...
echo ===================================================
start "Trading Alerts Backend" cmd /k "cd backend && .venv\Scripts\activate && uvicorn server:app --host 127.0.0.1 --port 8000"

timeout /t 2 /nobreak > nul

echo ===================================================
echo Starting Trading Alerts Frontend (Next.js)...
echo ===================================================
start "Trading Alerts Frontend" cmd /k "cd frontend && npm run dev"

echo.
echo Both servers are booting up.
echo The dashboard will automatically be available at: http://localhost:3000
echo Close this window at any time.
pause
