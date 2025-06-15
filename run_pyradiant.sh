#!/bin/bash

# Activate Conda
eval "$(conda shell.bash hook)"
conda activate pyradiant_env

# Optionally enable software OpenGL to avoid issues
# export QT_OPENGL=software

# Launch PyRadiant
python run_pyradiant.py