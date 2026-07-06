#!/usr/bin/env bash
# =============================================================================
# qe-agent — One-shot Bootstrap (convenience wrapper)
#
# Calls cfn/setup-infra.sh (one-time AWS setup + CFN stack) followed by
# cfn/deploy.sh (backend image push + frontend Amplify deploy). Use this for
# first-time deploys when you want everything provisioned and running in one
# command.
#
# After the first run, you can call the two scripts independently:
#   - bash cfn/setup-infra.sh   when the CFN template changes (or to rotate keys)
#   - bash cfn/deploy.sh        when application code changes
#
# Run from the project root: bash cfn/bootstrap.sh
# =============================================================================
set -e
sed -i 's/\r//' "$0" 2>/dev/null || true

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

bash "$SCRIPT_DIR/setup-infra.sh"
bash "$SCRIPT_DIR/deploy.sh"

echo ""
echo "============================================================"
echo "  Bootstrap complete (infra + application)."
echo "============================================================"
