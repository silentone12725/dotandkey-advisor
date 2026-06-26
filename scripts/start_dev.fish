#!/usr/bin/env fish
# scripts/start_dev.fish
#
# One-command dev startup for the Dot & Key advisor backend:
#   1. Activates (or creates) the virtualenv
#   2. Starts the FalkorDB container if it isn't already running
#   3. Waits until FalkorDB/Redis actually accepts connections
#   4. Starts uvicorn in the foreground
#
# Usage:
#   chmod +x scripts/start_dev.fish   (one-time)
#   ./scripts/start_dev.fish
#
# Ctrl+C stops uvicorn. The FalkorDB container keeps running in the
# background — run scripts/stop_dev.fish to stop it too.

set -l PROJECT_ROOT (cd (dirname (status --current-filename))/.. ; pwd)
cd $PROJECT_ROOT

echo "── Dot & Key Advisor — dev startup ──"

# ── 1. virtualenv ────────────────────────────────────────────────────────
echo "[1/4] Virtualenv..."
if not test -d .venv
    echo "      .venv not found — creating it now"
    python3 -m venv .venv
    source .venv/bin/activate.fish
    echo "      installing requirements..."
    pip install -q -r requirements.txt
else
    source .venv/bin/activate.fish
    echo "      activated"
end

# ── 2. FalkorDB container ────────────────────────────────────────────────
echo "[2/4] FalkorDB container..."
if docker ps --format '{{.Names}}' | grep -qx falkordb
    echo "      already running"
else if docker ps -a --format '{{.Names}}' | grep -qx falkordb
    echo "      found stopped container — starting it"
    docker start falkordb > /dev/null
else
    echo "      not found — creating new container"
    docker run -d --name falkordb -p 6379:6379 -p 3000:3000 falkordb/falkordb:latest > /dev/null
end

# ── 3. wait for redis to actually accept connections ────────────────────
echo "[3/4] Waiting for FalkorDB/Redis to accept connections..."
set -l tries 0
while true
    if python3 -c "import redis; redis.Redis(host='localhost', port=6379, socket_connect_timeout=1).ping()" 2>/dev/null
        break
    end
    set tries (math $tries + 1)
    if test $tries -ge 30
        echo "      ERROR: not ready after 30s. Check: docker logs falkordb"
        exit 1
    end
    sleep 1
end
echo "      ready"

# ── .env sanity check ────────────────────────────────────────────────────
if not test -f .env
    echo
    echo "      WARNING: .env not found."
    echo "      Run: cp .env.example .env   then add your NIM_API_KEY"
    echo
end

# ── 3b. refresh promotions + product media ────────────────────────────────────────
echo "[3b/4] Refreshing promotions data..."
python3 scripts/scrape_promotions.py 2>/dev/null
and echo "       promotions.json updated"
or  echo "       promotions update skipped (non-fatal)"

if test -f scripts/fetch_product_media.py
    set -l is_stale true
    if test -f data/product_media.json
        set -l age (math (date +%s) - (stat -c %Y data/product_media.json 2>/dev/null; or echo 0))
        if test $age -lt 86400
            set is_stale false
        end
    end
    if test $is_stale = true
        echo "       product_media.json stale - refreshing..."
        python3 scripts/fetch_product_media.py 2>/dev/null
        and echo "       product_media.json updated"
        or  echo "       media fetch skipped (non-fatal)"
    else
        echo "       product_media.json fresh - skipping"
    end
end

# ── 4. start uvicorn (foreground) ────────────────────────────────────────
echo "[4/4] Starting FastAPI backend on :8000 ..."
echo "── Ctrl+C stops the backend. FalkorDB keeps running in the background. ──"
echo "── Run scripts/stop_dev.fish to stop FalkorDB too. ──"
echo
uvicorn backend.app:app --reload --port 8000