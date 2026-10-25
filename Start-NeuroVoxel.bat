@echo off
docker version >nul 2>&1 || (echo Start Docker Desktop first. & pause & exit /b 1)
docker compose up -d --build || (pause & exit /b 1)
start "" http://127.0.0.1:8000
echo NeuroVoxel is running. Stop with: docker compose down
pause
