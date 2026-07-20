@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo Python was not found on this PC - installing it now...
    where winget >nul 2>nul
    if %errorlevel% neq 0 (
        echo.
        echo winget isn't available on this PC. Please install Python manually from:
        echo https://www.python.org/downloads/
        echo IMPORTANT: tick "Add python.exe to PATH" during setup, then run this file again.
        pause
        exit /b 1
    )
    winget install --id Python.Python.3.12 -e --silent --accept-package-agreements --accept-source-agreements
    if %errorlevel% neq 0 (
        echo.
        echo Automatic install failed. Please install Python manually from:
        echo https://www.python.org/downloads/
        echo ^(tick "Add python.exe to PATH" during setup^), then run this file again.
        pause
        exit /b 1
    )
    echo.
    echo Python installed successfully.
    echo Please close this window and double-click run.bat again to finish setup.
    pause
    exit /b 0
)

echo Checking required packages...
python -m pip install --quiet --disable-pip-version-check -r requirements.txt
python -m streamlit run app.py
pause
