#!/bin/bash
# Quick runner for Polla Mundialista predictions
# Usage: ./polla.sh [date] [--knockout]
#
# Examples:
#   ./polla.sh                    # Today's World Cup predictions
#   ./polla.sh 2026-06-12        # Specific date
#   ./polla.sh --knockout        # Knockout stage scoring
#   ./polla.sh 2026-06-28 --knockout  # Knockout round on specific date

SPORT="soccer_world_cup"
DATE=""
KNOCKOUT=""

for arg in "$@"; do
    case $arg in
        --knockout)
            KNOCKOUT="--knockout"
            ;;
        20[0-9][0-9]-[0-9][0-9]-[0-9][0-9])
            DATE="--date $arg"
            ;;
        *)
            echo "Usage: ./polla.sh [YYYY-MM-DD] [--knockout]"
            exit 1
            ;;
    esac
done

echo "🏆 Fetching World Cup predictions..."
echo ""

uv run python -m src.main --sport "$SPORT" --polla $DATE $KNOCKOUT
