#!/bin/bash

cd ~/Coding/tax-estimator
python3 -m venv venv
source venv/bin/activate
pip install reportlab pillow pytesseract

PID=$(lsof -ti tcp:8001 -sTCP:LISTEN)
if [ -n "$PID" ]; then
  kill $PID
  sleep 1
  kill -0 $PID 2>/dev/null && kill -9 $PID   # force it if it's still alive
fi

nohup python3 -u server.py > server.log 2>&1 &