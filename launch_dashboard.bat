@echo off
setlocal
set SCRIPT_DIR=%~dp0
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%launch_dashboard.ps1"
if errorlevel 1 (
  echo.
  echo Echec du lancement. Appuyez sur une touche pour fermer.
  pause >nul
)
endlocal
