@echo off
REM Build a distributable Inventory Hub release on Windows.
REM
REM Produces release\InventoryHub-<version>-windows-x64.zip plus SHA256SUMS.txt
REM
REM No code-signing certificate is used, so SmartScreen will warn on first run.
REM Ship INSTALL.md with the download so customers know to expect it.
setlocal enabledelayedexpansion
cd /d "%~dp0"

if "%VERSION%"=="" set /p VERSION=<VERSION
set INVL_VERSION=%VERSION%
set OUT=release

call build.bat
if errorlevel 1 exit /b 1

if not exist "%OUT%" mkdir "%OUT%"
del /q "%OUT%\*.zip" "%OUT%\SHA256SUMS.txt" 2>nul

set ZIP=%OUT%\InventoryHub-%VERSION%-windows-x64.zip
echo Building %ZIP% ...

REM Stage the app plus the install notes, then zip the whole folder.
set STAGE=%TEMP%\invhub-stage
if exist "%STAGE%" rmdir /s /q "%STAGE%"
mkdir "%STAGE%"
xcopy /e /i /q "dist\InventoryHub" "%STAGE%\InventoryHub" >nul
copy /y "INSTALL.md" "%STAGE%\READ ME FIRST.md" >nul

powershell -NoProfile -Command ^
  "Compress-Archive -Path '%STAGE%\*' -DestinationPath '%ZIP%' -Force"
rmdir /s /q "%STAGE%"

REM Publish this hash on the download page — with no signature it is the only
REM way a customer can tell a good download from a bad one.
powershell -NoProfile -Command ^
  "Get-FileHash -Algorithm SHA256 '%ZIP%' | ForEach-Object { $_.Hash + '  ' + (Split-Path $_.Path -Leaf) } | Set-Content '%OUT%\SHA256SUMS.txt'"

echo.
echo Release artifacts in %OUT%\:
dir /b "%OUT%"
echo.
echo Publish the .zip AND the SHA256 hash. Ship INSTALL.md with the download link.
endlocal
