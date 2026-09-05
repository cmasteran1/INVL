@echo off
REM Build the standalone Inventory Hub desktop app on Windows.
REM Produces dist\InventoryHub\InventoryHub.exe
setlocal
cd /d "%~dp0"

if not exist ".venv" (
  python -m venv .venv
)
call .venv\Scripts\activate.bat
pip install --quiet --upgrade pip
pip install --quiet -r requirements-desktop.txt

echo Building... (this can take a minute)
pyinstaller --noconfirm --clean inventory_hub.spec

echo Done -^> dist\InventoryHub\InventoryHub.exe
endlocal
