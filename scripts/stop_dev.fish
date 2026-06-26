#!/usr/bin/env fish
# scripts/stop_dev.fish
#
# Stops the FalkorDB container. uvicorn stops separately via Ctrl+C
# in whichever terminal is running start_dev.fish.
#
# Usage:
#   chmod +x scripts/stop_dev.fish   (one-time)
#   ./scripts/stop_dev.fish

echo "Stopping FalkorDB container..."
docker stop falkordb > /dev/null
echo "Done. Data is preserved (container not removed) — start_dev.fish will reuse it next time."