#!/usr/bin/env bash
# Hardware-free smoke test for the power-measurement Skill pipeline.
# Prints a PASS banner and exits 0 on success; non-zero otherwise.

set -euo pipefail

cd "$(dirname "$0")/.."

echo "[tests_e2e] running pytest tests_e2e/ ..."
python -m pytest tests_e2e -v

echo ""
echo "=========================================="
echo "[tests_e2e] ALL CHECKS PASSED"
echo "=========================================="
