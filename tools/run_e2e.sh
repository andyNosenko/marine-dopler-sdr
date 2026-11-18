#!/bin/bash
set -e
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DB=/tmp/test_e2e.db
LOG=/tmp/test_e2e_main.log
rm -f "$DB" "$DB"-wal "$DB"-shm "$LOG"

cd "$REPO_ROOT/app"
PYTHONPATH="$REPO_ROOT" \
  MOCK_SDR=true \
  MOCK_BEARING_DEG=11.0 \
  MOCK_BEARING_INTERVAL_S=15 \
  MOCK_BEARING_COUNT=2 \
  DB_PATH="$DB" \
  timeout 45 python3 -u main.py > "$LOG" 2>&1 &
MAIN_PID=$!

BOUND=0
for i in $(seq 1 30); do
  if grep -q "UDP :10110" "$LOG" 2>/dev/null; then
    echo "[E2E] main.py bound UDP after ~$((i/2))s"
    BOUND=1
    break
  fi
  sleep 0.5
done

if [ "$BOUND" -ne 1 ]; then
  echo "[E2E] ERROR: main.py never bound UDP"
  cat "$LOG"
  kill $MAIN_PID 2>/dev/null || true
  exit 1
fi

cd "$REPO_ROOT"
python3 tools/ais_simulator.py --scenario tools/scenarios/basic.yaml &
AIS_PID=$!

wait $MAIN_PID 2>/dev/null || true
kill $AIS_PID 2>/dev/null || true
wait $AIS_PID 2>/dev/null || true

echo
echo "=== main.py log (last 20) ==="
tail -n 20 "$LOG" || true

echo
echo "=== DB result ==="
sqlite3 "$DB" "SELECT mmsi, imo, shipname, length, lat, lon, last_seen FROM vessels;" || echo "(empty)"
