@echo off
title CleanBot Quantum Desk — tunnel (keep this window open)
echo.
echo   CLEANBOT // QUANTUM DESK
echo   Opening secure tunnel to the Ireland server...
echo   Your browser will open in 3 seconds.
echo.
echo   Keep THIS window open while using the dashboard.
echo   Close it to disconnect.
echo.
start "" cmd /c "timeout /t 3 >nul & start http://localhost:8096"
ssh -i "C:\Users\leona\Projects\polymarket-no-maxi-bot\polymarket-key.pem" -o StrictHostKeyChecking=no -o ServerAliveInterval=30 -L 8096:localhost:8096 -L 8095:localhost:8095 ubuntu@34.255.2.158 -N
echo.
echo   Tunnel closed. Press any key to exit.
pause >nul
