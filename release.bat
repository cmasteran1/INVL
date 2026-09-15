@echo off
REM Build a distributable INVL Hub release on Windows.
REM
REM Produces release\INVLHub-<version>-windows-x64-setup.exe plus SHA256SUMS.txt
REM The installer (packaging\installer.iss) shows EULA.txt as a click-through
REM license: nothing is installed until the customer accepts it.
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
del /q "%OUT%\*.exe" "%OUT%\*.zip" "%OUT%\SHA256SUMS.txt" 2>nul

REM Inno Setup 6 is preinstalled on GitHub's windows runners; locally, install
REM it from jrsoftware.org.
set ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe
if not exist "%ISCC%" set ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe
if not exist "%ISCC%" (
  echo error: Inno Setup 6 not found - install it from jrsoftware.org >&2
  exit /b 1
)

echo Building the installer...
"%ISCC%" /Q packaging\installer.iss
if errorlevel 1 exit /b 1

set SETUP=%OUT%\INVLHub-%VERSION%-windows-x64-setup.exe
if not exist "%SETUP%" (
  echo error: %SETUP% was not produced >&2
  exit /b 1
)

REM Publish this hash on the download page - with no signature it is the only
REM way a customer can tell a good download from a bad one.
powershell -NoProfile -Command ^
  "Get-FileHash -Algorithm SHA256 '%SETUP%' | ForEach-Object { $_.Hash + '  ' + (Split-Path $_.Path -Leaf) } | Set-Content '%OUT%\SHA256SUMS.txt'"

echo.
echo Release artifacts in %OUT%\:
dir /b "%OUT%"
echo.
echo Publish the setup .exe AND the SHA256 hash. Ship INSTALL.md with the download link.
endlocal
