@echo off
REM Create the pyradiant_env conda environment and install all dependencies.
REM Requires: conda already installed and on PATH.

setlocal

set ENV_NAME=pyradiant_env
set PY_VERSION=3.12

cd /d "%~dp0"

call conda env list | findstr /B /C:"%ENV_NAME% " >nul
if %ERRORLEVEL%==0 (
    echo Environment '%ENV_NAME%' already exists.
    set /p REPLY="Remove and recreate? [y/N] "
    if /I "%REPLY%"=="y" (
        call conda env remove -n %ENV_NAME% -y
    ) else (
        echo Reusing existing environment.
        goto :install
    )
)

echo Creating conda env '%ENV_NAME%' (python=%PY_VERSION%)...
call conda create -n %ENV_NAME% python=%PY_VERSION% pip -y
if errorlevel 1 goto :fail

:install
call conda activate %ENV_NAME%
if errorlevel 1 goto :fail

echo Installing requirements.txt...
call pip install -r requirements.txt
if errorlevel 1 goto :fail

echo Installing pyqtdarktheme (ignoring strict python version metadata)...
call pip install --ignore-requires-python pyqtdarktheme==2.1.0
if errorlevel 1 goto :fail

echo Installing pyepics (optional, for EPICS integration)...
call pip install pyepics
if errorlevel 1 echo   (pyepics install failed - EPICS features will be disabled)

echo.
echo Done. Activate with:  conda activate %ENV_NAME%
echo Then run:             python run_pyradiant.py
goto :eof

:fail
echo.
echo Setup failed. See errors above.
exit /b 1
