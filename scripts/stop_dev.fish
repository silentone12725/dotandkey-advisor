#!/usr/bin/env fish
# scripts/stop_dev.fish
#
# Stops the FalkorDB container. uvicorn stops separately via Ctrl+C
# in whichever terminal is running start_dev.fish.
#
# Usage:
#   chmod +x scripts/stop_dev.fish   (one-time)
#   ./scripts/stop_dev.fish

echo "Stopping FalkorDB (via docker compose)..."
docker compose stop falkordb > /dev/null
echo "Done. Data is preserved in the falkordb_data volume — start_dev.fish will restart it next time."