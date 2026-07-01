#!/bin/bash
# Daily Polla Mundialista auto-updater
# Runs the prediction pipeline with fresh odds and submits to golpredictor.com
# 
# Setup (run once):
#   chmod +x scripts/daily_update.sh
#   # Add to launchd or crontab:
#   crontab -e
#   # Add: 0 8 * * * cd /Users/nilsonnieto/Documents/betting-house-reading && ./scripts/daily_update.sh >> /tmp/polla-update.log 2>&1
#
# Or just run manually: ./scripts/daily_update.sh

set -e

cd "$(dirname "$0")/.."

echo "=========================================="
echo "  POLLA UPDATE — $(date '+%Y-%m-%d %H:%M')"
echo "=========================================="

# 1. Generate fresh predictions with latest odds (knockout mode)
echo ""
echo "→ Fetching fresh odds and generating predictions..."
uv run python -m src --sport soccer_fifa_world_cup --knockout 2>&1 | grep -v "^INFO:" | grep -v "^WARNING:"

# 2. Submit to golpredictor.com
echo ""
echo "→ Submitting to golpredictor.com..."
uv run python scripts/submit_polla.py predictions.json 2>&1 | grep -E "(Loaded|Skipped|UPDATING|NEW|ALREADY|Done|saved|Error|Logged)"

# 3. Collect results for tracking
echo ""
echo "→ Collecting latest results..."
uv run python -c "
from src.main import main
main(['--sport', 'soccer_fifa_world_cup', '--retro'])
" 2>&1 | grep -E "(Collected|Total Polla|Matches analyzed|Result accuracy|Exact score)"

echo ""
echo "✅ Update complete — $(date '+%H:%M')"
echo "=========================================="
