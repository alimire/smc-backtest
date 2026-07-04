#!/bin/bash
# Regenerate scanner output, simulate outcomes, and build Salim review doc.
set -euo pipefail
cd "$(dirname "$0")/.."

DAYS="${1:-60}"
SESSION="${2:-both}"

echo "=== 1/3 Scan (Salim rules) ==="
python3 smc_detector.py --days "$DAYS" --session "$SESSION" --report json --output smc_report

echo "=== 2/3 Simulate A+ outcomes ==="
python3 scripts/simulator.py --days "$DAYS"

echo "=== 3/3 Expert review for Salim ==="
python3 scripts/generate_review.py --days "$DAYS"

echo "Done. Share reports/expert_review.md with Salim."
