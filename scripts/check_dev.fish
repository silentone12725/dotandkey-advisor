#!/usr/bin/env fish
# scripts/check_dev.fish
#
# Quick diagnostic — checks each piece of the stack independently so
# you know exactly what's down without reading a stack trace.
#
# Usage:
#   chmod +x scripts/check_dev.fish   (one-time)
#   ./scripts/check_dev.fish

set -l PROJECT_ROOT (cd (dirname (status --current-filename))/.. ; pwd)
cd $PROJECT_ROOT

echo "── Dot & Key Advisor — health check ──"
echo

# venv
if test -d .venv
    echo "[venv]      found"
else
    echo "[venv]      MISSING — run: python3 -m venv .venv"
end

# .env
if test -f .env
    echo "[.env]      found"
    if grep -q "nvapi-xxxxxxxxxxxxxxxxxxxx" .env
        echo "            WARNING: still contains the placeholder NIM key"
    end
else
    echo "[.env]      MISSING — run: cp .env.example .env"
end

# docker
if docker ps --format '{{.Names}}' | grep -qx dotandkey-falkordb
    echo "[falkordb]  running (compose)"
else if docker ps -a --format '{{.Names}}' | grep -qx dotandkey-falkordb
    echo "[falkordb]  stopped — run: docker compose up falkordb -d"
else
    echo "[falkordb]  not found — run start_dev.fish to create it"
end

# redis connectivity (needs venv active)
if test -d .venv
    source .venv/bin/activate.fish
    if python3 -c "import redis; redis.Redis(host='localhost', port=6379, socket_connect_timeout=2).ping()" 2>/dev/null
        echo "[redis]     reachable on :6379"
    else
        echo "[redis]     NOT reachable on :6379"
    end

    # graph data check
    set -l product_count (python3 -c "
from falkordb import FalkorDB
try:
    g = FalkorDB(host='localhost', port=6379).select_graph('dotandkey')
    r = g.query('MATCH (p:Product) RETURN count(p) AS n')
    print(r.result_set[0][0])
except Exception as e:
    print('ERROR:', e)
" 2>/dev/null)
    echo "[graph]     $product_count products in 'dotandkey' graph"
else
    echo "[redis]     skipped (no venv)"
    echo "[graph]     skipped (no venv)"
end

# backend reachability
if curl -s -o /dev/null -w '' http://localhost:8000/health 2>/dev/null
    set -l health (curl -s http://localhost:8000/health)
    echo "[backend]   running — $health"
else
    echo "[backend]   not running — run: ./scripts/start_dev.fish"
end

echo
echo "── done ──"