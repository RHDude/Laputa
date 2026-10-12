#!/bin/bash
# Управление стендом: bench.sh start | stop
S=${LAPUTA_WORK:-/tmp/laputa-work}; mkdir -p "$S"
cd "$(dirname "$0")/.."
stop() { pkill -f 'bench/run\.py serve'; pkill -f 'laputa\.py (hub|agent)'; sleep 1; }
case "$1" in
  stop) stop ;;
  start) stop; BENCH_DIR=$S/bench nohup python3 test/bench/run.py serve > $S/serve.out 2>&1 & sleep 8; pgrep -fc 'laputa\.py (hub|agent)' ;;
esac
