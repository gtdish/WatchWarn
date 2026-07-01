#!/bin/zsh

source /opt/miniconda3/etc/profile.d/conda.sh
conda activate watchwarn

cd /Users/jd/Documents/Projects/WatchWarn

cp app_national.py app.py

python -m uvicorn app:app --reload