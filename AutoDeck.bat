@echo off
REM ============================================================
REM  AutoDeck - one button. Opens the review UI in your browser.
REM  Everything is found relative to THIS folder, so the whole
REM  AutoDeck folder can be moved or copied anywhere.
REM ============================================================
setlocal EnableExtensions EnableDelayedExpansion
title AutoDeck

cd /d "%~dp0"

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

set "AUTODECK2_ROOT=%ROOT%\engine"
set "AUTODECK_V1_ROOT=%ROOT%\engine-v1"
set "APP_DIR=%ROOT%\app"
set "VENV_DIR=%ROOT%\.venv"
set "PYTHON_EXE=%VENV_DIR%\Scripts\python.exe"
set "PYTHONPATH=%AUTODECK_V1_ROOT%\src;%AUTODECK2_ROOT%;%APP_DIR%"

echo ============================================================
echo   AutoDeck - starting
echo ============================================================
echo.

REM ---- sanity: are all three parts present? ----------------
if not exist "%APP_DIR%\autodeck_app\__main__.py" goto MISSING_APP
if not exist "%AUTODECK2_ROOT%\autodeck2" goto MISSING_ENGINE
if not exist "%AUTODECK_V1_ROOT%\src\autodeck" goto MISSING_V1

REM ---- already set up? -------------------------------------
if exist "%PYTHON_EXE%" goto RUN

REM ---- find a real Python 3.12+ ----------------------------
echo First-time setup. This happens once and needs the internet.
echo It takes a few minutes.
echo.

set "BASE_PYTHON="
call :TryPython "py" "-3.12"
if not defined BASE_PYTHON call :TryPython "py" "-3.13"
if not defined BASE_PYTHON call :TryPython "py" "-3"
if not defined BASE_PYTHON call :TryPython "python" ""
if not defined BASE_PYTHON call :TryPython "python3" ""
if not defined BASE_PYTHON goto NO_PYTHON

echo Using Python: !BASE_PYTHON! !BASE_PYARG!
echo Creating the private Python environment...
!BASE_PYTHON! !BASE_PYARG! -m venv "%VENV_DIR%"
if not exist "%PYTHON_EXE%" goto VENV_FAILED

echo Installing libraries...
"%PYTHON_EXE%" -m pip install --disable-pip-version-check --upgrade pip >nul
if errorlevel 1 goto INSTALL_FAILED

echo   [1/3] analysis engine (v1)
"%PYTHON_EXE%" -m pip install --disable-pip-version-check -e "%AUTODECK_V1_ROOT%[rhino]"
if errorlevel 1 goto INSTALL_FAILED

echo   [2/3] outline / auto-fit engine
"%PYTHON_EXE%" -m pip install --disable-pip-version-check --no-deps -e "%AUTODECK2_ROOT%"
if errorlevel 1 goto INSTALL_FAILED

echo   [3/3] review app
"%PYTHON_EXE%" -m pip install --disable-pip-version-check "libigl==2.6.1" -e "%APP_DIR%"
if errorlevel 1 goto INSTALL_FAILED

echo.
echo Setup complete.
echo.

:RUN
echo Opening AutoDeck in your browser...
echo Leave this window open - closing it stops AutoDeck.
echo.
"%PYTHON_EXE%" -m autodeck_app %*
set "RUN_EXIT=%ERRORLEVEL%"
echo.
if not "%RUN_EXIT%"=="0" (
  echo AutoDeck exited with code %RUN_EXIT%.
  echo.
)
pause
exit /b %RUN_EXIT%


REM ============================================================
REM  :TryPython <command> <version-arg>
REM  Sets BASE_PYTHON/BASE_PYARG only if the command is a REAL
REM  Python 3.12+. The Microsoft Store "python.exe" stub fails
REM  this probe, which is exactly what we want.
REM ============================================================
:TryPython
set "_CMD=%~1"
set "_ARG=%~2"
where "%_CMD%" >nul 2>nul || goto :eof
%_CMD% %_ARG% -c "import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)" >nul 2>nul
if errorlevel 1 goto :eof
set "BASE_PYTHON=%_CMD%"
set "BASE_PYARG=%_ARG%"
goto :eof


:MISSING_APP
echo The review app is missing from:
echo   %APP_DIR%
echo This folder should contain autodeck_app\. Restore it and try again.
echo.
pause
exit /b 2

:MISSING_ENGINE
echo The AutoDeck2 engine is missing from:
echo   %AUTODECK2_ROOT%
echo.
pause
exit /b 2

:MISSING_V1
echo The v1 analysis engine is missing from:
echo   %AUTODECK_V1_ROOT%
echo.
pause
exit /b 2

:NO_PYTHON
echo ------------------------------------------------------------
echo   Python 3.12 is not installed on this computer.
echo ------------------------------------------------------------
echo.
echo AutoDeck needs it. Install it once:
echo.
echo   1. Go to  https://www.python.org/downloads/
echo   2. Download Python 3.12 (or newer) for Windows
echo   3. IMPORTANT: tick "Add python.exe to PATH" on the first
echo      screen of the installer
echo   4. Finish the install, then double-click AutoDeck again
echo.
echo (If Windows opens the Microsoft Store when you type "python",
echo  that is a placeholder, not real Python - install from
echo  python.org as above.)
echo.
set /p "OPENIT=Open the Python download page now? [Y/N] "
if /i "!OPENIT!"=="Y" start "" "https://www.python.org/downloads/"
exit /b 2

:VENV_FAILED
echo Could not create the Python environment at:
echo   %VENV_DIR%
echo Delete that folder if it exists and try again.
echo.
pause
exit /b 2

:INSTALL_FAILED
echo ------------------------------------------------------------
echo   Setup did not finish.
echo ------------------------------------------------------------
echo.
echo Check your internet connection, then delete this folder:
echo   %VENV_DIR%
echo and double-click AutoDeck again.
echo.
pause
exit /b 2
