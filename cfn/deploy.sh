#!/usr/bin/env bash
# =============================================================================
# qe-agent — Combined Deploy
#
# Convenience wrapper that runs deploy-backend.sh then deploy-frontend.sh.
# Use this on every code change. Requires the CFN stack to already exist —
# run 'bash cfn/setup-infra.sh' first if it doesn't.
#
# Run from the project root: bash cfn/deploy.sh
# =============================================================================
set -e
sed -i 's/\r//' "$0" 2>/dev/null || true

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

bash "$SCRIPT_DIR/deploy-backend.sh"
bash "$SCRIPT_DIR/deploy-frontend.sh"

echo ""
echo "============================================================"
echo "  Full deploy complete (backend + frontend)."
echo "============================================================"
