@echo off
call conda activate pyradiant_env
REM Optional: enable software OpenGL
REM set QT_OPENGL=software
python run_pyradiant.py
pause