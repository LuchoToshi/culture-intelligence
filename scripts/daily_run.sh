#!/bin/zsh
# Daily pipeline: collect new content, analyze it, update the signal registry.
# Invoked by launchd (com.culture-intelligence.daily). Weekly reports stay manual.
set -u
export PATH="/Users/shariffshariff/.local/bin:/opt/homebrew/bin:/usr/bin:/bin"
cd /Users/shariffshariff/Documents/Claude/culture-intelligence || exit 1

echo "=== daily run started: $(date '+%Y-%m-%d %H:%M:%S') ==="
uv run culture ingest
ingest_rc=$?
uv run culture analyze
analyze_rc=$?
uv run culture discover
uv run culture lifecycle
echo "=== daily run finished: $(date '+%Y-%m-%d %H:%M:%S') (ingest=$ingest_rc analyze=$analyze_rc) ==="
