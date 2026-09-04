#!/bin/sh
set -eu

Xvfb :99 -screen 0 1920x1080x24 -ac +extension GLX +render -noreset >/tmp/xvfb.log 2>&1 &
sleep 1
openbox --sm-disable >/tmp/openbox.log 2>&1 &

exec uvicorn unified_api.main:app --host 0.0.0.0 --port 8000
