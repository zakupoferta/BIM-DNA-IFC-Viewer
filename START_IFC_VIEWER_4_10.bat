@echo off
setlocal EnableExtensions EnableDelayedExpansion
title BIM DNA IFC Viewer 4.10
cd /d "%~dp0"

echo ============================================================
echo        BIM DNA IFC VIEWER 4.10
echo ============================================================
echo.

set "PYEXE="

where py >nul 2>nul
if not errorlevel 1 (
  for /f "delims=" %%P in ('py -3.13 -c "import sys; print(sys.executable)" 2^>nul') do set "PYEXE=%%P"
)
if not defined PYEXE if exist "%LocalAppData%\Programs\Python\Python313\python.exe" set "PYEXE=%LocalAppData%\Programs\Python\Python313\python.exe"
if not defined PYEXE (
  for /f "delims=" %%P in ('where python 2^>nul') do (
    if not defined PYEXE set "PYEXE=%%P"
  )
)
if not defined PYEXE goto INSTALL_PYTHON

:CHECK_PYTHON
echo Python:
"%PYEXE%" --version
if errorlevel 1 (
  set "PYEXE="
  goto INSTALL_PYTHON
)
goto CHECK_IFC

:INSTALL_PYTHON
echo.
echo Python nie jest zainstalowany.
echo.
echo Pobieram oficjalny instalator Python 3.13.15 z python.org...
set "PYURL=https://www.python.org/ftp/python/3.13.15/python-3.13.15-amd64.exe"
set "PYINSTALLER=%TEMP%\python-3.13.15-amd64.exe"
where curl >nul 2>nul
if errorlevel 1 (
  echo Otworz https://www.python.org/downloads/windows/
  pause
  exit /b 1
)
curl -L --fail --output "%PYINSTALLER%" "%PYURL%"
if errorlevel 1 ( pause & exit /b 1 )
"%PYINSTALLER%" /quiet InstallAllUsers=0 PrependPath=0 Include_test=0
if errorlevel 1 ( pause & exit /b 1 )
set "PYEXE=%LocalAppData%\Programs\Python\Python313\python.exe"
del "%PYINSTALLER%" >nul 2>nul

:CHECK_IFC
echo.
echo Sprawdzam IfcOpenShell...
"%PYEXE%" -c "import ifcopenshell" >nul 2>nul
if errorlevel 1 (
  "%PYEXE%" -m pip install --upgrade pip
  "%PYEXE%" -m pip install ifcopenshell==0.8.5
  if errorlevel 1 ( pause & exit /b 1 )
)
echo.
echo Uruchamiam BIM DNA IFC Viewer 4.10...
"%PYEXE%" launcher.py
if errorlevel 1 ( echo Viewer zakonczyl sie bledem. & pause )
