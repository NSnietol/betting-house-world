#!/bin/bash
# Setup script for Polla Mundialista predictor
# Run this once after cloning the repo

set -e

echo "🏆 Polla Mundialista — Setup"
echo "============================"
echo ""

# Check for uv
if ! command -v uv &> /dev/null; then
    echo "❌ 'uv' is not installed."
    echo "   Install it with: curl -LsSf https://astral.sh/uv/install.sh | sh"
    echo "   Or: brew install uv"
    exit 1
fi
echo "✅ uv found: $(uv --version)"

# Check Python version
PYTHON_VERSION=$(python3 --version 2>&1 | grep -oP '\d+\.\d+' | head -1)
echo "✅ Python: $(python3 --version)"

# Sync dependencies
echo ""
echo "📦 Installing dependencies..."
uv sync
echo "✅ Dependencies installed"

# Run tests to verify
echo ""
echo "🧪 Running tests..."
if uv run pytest tests/ -q --tb=short 2>&1 | tail -3; then
    echo "✅ All tests pass"
else
    echo "⚠️  Some tests failed — check output above"
fi

# Verify live connectivity
echo ""
echo "🌐 Testing bookmaker connectivity..."
uv run python -c "
from src.adapters.unibet import UnibetAdapter
a = UnibetAdapter()
health = a.health_check()
print(f'   Kambi/Unibet API: {health.value}')
if health.value == 'reachable':
    print('   ✅ Ready to fetch live odds!')
else:
    print('   ⚠️  API not reachable — check your network connection')
"

echo ""
echo "============================"
echo "✅ Setup complete!"
echo ""
echo "Usage:"
echo "  ./polla.sh              # Get today's World Cup predictions"
echo "  ./polla.sh 2026-06-12   # Specific date"
echo "  ./polla.sh --knockout   # Knockout stage scoring"
echo ""
