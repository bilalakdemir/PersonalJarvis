@echo off
REM Personal Jarvis — DEV INSTANCE launcher (Windows).
REM
REM Starts a SECOND desktop app beside the regular one, from this same checkout:
REM "Personal Jarvis Dev" — own data dir (data-dev\), own ports (+100), DEV-badged
REM icon, no wake word / global hotkeys / chat channels / autostart (those stay
REM with the regular app). Restart it as often as you like; the regular app and
REM the coding sessions inside it are never touched.
REM
REM Not the same as dev.bat / run.bat --dev (those load the frontend from a Vite
REM HMR server). This one runs the built frontend exactly like run.bat does.
REM Same as: set JARVIS_INSTANCE=dev && run.bat

setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
)

if exist "scripts\check-working-tree.ps1" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\check-working-tree.ps1"
)

python -m jarvis.ui.web.frontend_freshness --repo-root "%CD%" --marker "data-dev\frontend-build.json"
if errorlevel 1 (
    echo Personal Jarvis Dev: frontend build freshness check failed.
    exit /b 1
)

REM AERION Self-Engineering v1: DEV-only sidecar. It watches canonical HUD
REM failures, repairs only inside isolated git worktrees, and uses the existing
REM Codex ChatGPT login (never OPENAI_API_KEY). Set AERION_SELF_ENGINEERING=0
REM before launch for an explicit opt-out.
if /I not "%AERION_SELF_ENGINEERING%"=="0" (
    start "" pythonw -m jarvis.self_engineering --repo-root "%CD%" --data-dir "%CD%\data-dev" --auto-merge-low-risk
)

if "%1"=="--debug" (
    set JARVIS_DEBUG=1
    python -m jarvis.ui.web.launcher --instance dev
) else if "%1"=="--headless" (
    python -m jarvis.ui.web.launcher --instance dev --headless
) else (
    start "" pythonw -m jarvis.ui.web.launcher --instance dev
)

endlocal
