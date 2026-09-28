#!/usr/bin/env bash
# =============================================================================
# qe-agent — Backend Deploy (re-runnable)
#
# 1. Builds the backend Docker image (linux/amd64 — EC2 nodes are x86_64)
# 2. Pushes it to ECR as :latest
# 3. Triggers /opt/qe-agent/run.sh on the EC2 via SSM Run Command, which
#    pulls the new image, refreshes env from SSM, and restarts the container.
# 4. Smoke-tests the public API URL.
#
# Run from the project root: bash cfn/deploy-backend.sh
# =============================================================================
set -e
sed -i 's/\r//' "$0" 2>/dev/null || true
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'

REGION="ap-south-1"
STACK_NAME="qe-agent"
REPO_NAME="qe-agent/backend"
ENV_FILE="$(dirname "$0")/../.env"

# Keep in sync with cfn/setup-infra.sh
BACKEND_ENV_KEYS=(
  DEV_USER_ID
  LLM_PROVIDER
  LLM_MODEL
  OPENAI_API_KEY
  ANTHROPIC_API_KEY
  EMBEDDING_PROVIDER
  VOYAGE_API_KEY
  VERIFICATION_MAX_CONCURRENCY
  VERIFICATION_TOOL_RESULT_WINDOW
  VERIFICATION_COMPACT_TREE
  VERIFICATION_ESCALATE_INCONCLUSIVE
  OLLAMA_BASE_URL
  OLLAMA_MODEL
  BDD_MODEL_PROVIDER
  FINE_TUNED_MODEL_ENDPOINT
  FINE_TUNED_MODEL_API_KEY
  FINE_TUNED_MODEL_TIMEOUT_SECONDS
  FINE_TUNED_ALLOW_FALLBACK
  FINE_TUNED_OLLAMA_MODEL
  TRAINING_DATA_OPT_IN
  CREDENTIAL_ENCRYPTION_KEY
  DATABASE_URL
  DIRECT_DATABASE_URL
  DB_POOL_SIZE
  DB_MAX_OVERFLOW
  PINECONE_API_KEY
  PINECONE_INDEX_NAME
  RAG_MIN_SCORE
  JIRA_BASE_URL
  JIRA_API_TOKEN
  JIRA_USER_EMAIL
  GITHUB_ACCESS_TOKEN
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY
  SUPABASE_ANON_KEY
  SUPABASE_BUCKET
  CONFLUENCE_BASE_URL
  CONFLUENCE_API_TOKEN
  CONFLUENCE_USER_EMAIL
  KAGGLE_USERNAME
  KAGGLE_KEY
  TRAINING_REPO_ROOT
  TRAINING_POLL_SECONDS
  TRAINING_TIMEOUT_SECONDS
  DEBUG
  DISABLE_DOCS
)

echo ""
echo "============================================================"
echo "  qe-agent — Backend Deploy"
echo "============================================================"

# ── 0. Verify prerequisites ──────────────────────────────────────────────────
if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker not installed. Start Docker Desktop and retry."
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "ERROR: docker daemon not running. Start Docker Desktop and retry."
  exit 1
fi

STACK_STATUS=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" --region "$REGION" \
  --query 'Stacks[0].StackStatus' --output text 2>/dev/null || echo "")
if [ -z "$STACK_STATUS" ] || [[ "$STACK_STATUS" == *"FAILED"* ]] || [[ "$STACK_STATUS" == *"ROLLBACK"* ]]; then
  echo "ERROR: CFN stack '$STACK_NAME' is missing or in a bad state ($STACK_STATUS)."
  echo "       Run 'bash cfn/setup-infra.sh' first."
  exit 1
fi

# ── 1. Read stack outputs ────────────────────────────────────────────────────
echo ""
echo "=== Step 1: Reading stack outputs ==="
INSTANCE_ID=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='BackendInstanceId'].OutputValue" --output text)
SITE_URL=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='CloudFrontUrl'].OutputValue" --output text)
DIRECT_URL=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='BackendDirectUrl'].OutputValue" --output text)
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
REGISTRY="$ACCOUNT.dkr.ecr.$REGION.amazonaws.com"

echo "  Instance ID : $INSTANCE_ID"
echo "  Site URL    : $SITE_URL"
echo "  Direct URL  : $DIRECT_URL  (debug only)"
echo "  Registry    : $REGISTRY"

# ── 2. Build + push Docker image ─────────────────────────────────────────────
echo ""
echo "=== Step 2: Building backend image ==="
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "$REGISTRY"

IMAGE_TAG="$REGISTRY/$REPO_NAME:latest"
docker build --platform linux/amd64 -t "$IMAGE_TAG" ./backend
echo ""
echo "=== Step 2b: Pushing to ECR ==="
docker push "$IMAGE_TAG"
echo "  Pushed $IMAGE_TAG"

# Free up local disk
docker image prune -af >/dev/null 2>&1 || true

# ── 2c. Sync CORS origins to SSM ─────────────────────────────────────────────
# CORS must allow the frontend's public origin (the CloudFront URL). We
# compute it here instead of relying on .env so it always matches the live
# stack — and we include localhost for local dev.
echo ""
echo "=== Step 2c: Syncing CORS origins to SSM ==="
CORS_VALUE="http://localhost:3000,$SITE_URL"
aws ssm put-parameter \
  --name "/qe-agent/env/CORS_ALLOW_ORIGINS" \
  --value "$CORS_VALUE" \
  --type SecureString \
  --overwrite \
  --region "$REGION" >/dev/null
echo "  CORS_ALLOW_ORIGINS = $CORS_VALUE"

# ── 2d. Sync backend env vars to SSM ─────────────────────────────────────────
# On each deploy, mirror .env into /qe-agent/env/* so cloud runtime always
# matches local backend config changes.
echo ""
echo "=== Step 2d: Syncing backend env vars to SSM ==="

read_env() {
  local key="$1"
  [ -f "$ENV_FILE" ] || { echo ""; return; }
  grep -E "^${key}=" "$ENV_FILE" | head -1 | cut -d'=' -f2- | tr -d '\r'
}

put_ssm() {
  local name="$1"
  local value="$2"

  if [ -z "$value" ]; then
    aws ssm delete-parameter --name "$name" --region "$REGION" >/dev/null 2>&1 || true
    echo "  $name cleared (empty in .env)."
    return
  fi

  aws ssm put-parameter \
    --name "$name" \
    --value "$value" \
    --type SecureString \
    --overwrite \
    --region "$REGION" >/dev/null

  echo "  $name updated."
}

for KEY in "${BACKEND_ENV_KEYS[@]}"; do
  put_ssm "/qe-agent/env/$KEY" "$(read_env "$KEY")"
done

# ── 3. Trigger redeploy on EC2 via SSM Run Command ───────────────────────────
echo ""
echo "=== Step 3: Triggering redeploy on EC2 via SSM ==="

CMD_ID=$(aws ssm send-command \
  --region "$REGION" \
  --instance-ids "$INSTANCE_ID" \
  --document-name "AWS-RunShellScript" \
  --comment "qe-agent backend deploy" \
  --parameters 'commands=["/opt/qe-agent/run-backend.sh 2>&1"]' \
  --query 'Command.CommandId' --output text)
echo "  SSM Command ID: $CMD_ID"

echo "  Waiting for command to finish..."
for i in $(seq 1 60); do
  STATUS=$(aws ssm get-command-invocation \
    --command-id "$CMD_ID" \
    --instance-id "$INSTANCE_ID" \
    --region "$REGION" \
    --query 'Status' --output text 2>/dev/null || echo "Pending")
  case "$STATUS" in
    Success)
      echo "  SSM command succeeded."
      break
      ;;
    Failed|Cancelled|TimedOut)
      echo "  SSM command $STATUS. Output:"
      aws ssm get-command-invocation \
        --command-id "$CMD_ID" \
        --instance-id "$INSTANCE_ID" \
        --region "$REGION" \
        --query 'StandardErrorContent' --output text
      exit 1
      ;;
    *)
      printf "  attempt %s/60 — %s\r" "$i" "$STATUS"
      sleep 5
      ;;
  esac
done
echo ""

# ── 4. Smoke test ────────────────────────────────────────────────────────────
# Hit the EC2 directly (bypassing CloudFront) — faster feedback and avoids
# CloudFront's 10-min initial propagation delay on first stack create.
echo ""
echo "=== Step 4: Smoke test ==="
for i in $(seq 1 12); do
  HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 \
    "$DIRECT_URL/health" 2>/dev/null || echo "000")
  if [ "$HTTP_CODE" = "200" ]; then
    echo "  /health responded 200 ✓"
    break
  fi
  # Try /docs as a fallback (FastAPI ships this by default unless DISABLE_DOCS=true)
  HTTP_CODE_DOCS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 \
    "$DIRECT_URL/docs" 2>/dev/null || echo "000")
  if [ "$HTTP_CODE_DOCS" = "200" ]; then
    echo "  /docs responded 200 ✓ (no /health endpoint — that's fine)"
    break
  fi
  echo "  attempt $i/12 — got HTTP $HTTP_CODE on /health, waiting 10s..."
  sleep 10
done

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "========================================================"
echo "  Backend deployed!"
echo "========================================================"
echo ""
echo "  API URL    : $SITE_URL/api/v1/...  (via CloudFront, HTTPS)"
echo "  Direct URL : $DIRECT_URL  (bypasses CloudFront — debug only)"
echo "  Instance   : $INSTANCE_ID"
echo ""
echo "  Logs:"
echo "    aws ssm start-session --target $INSTANCE_ID --region $REGION"
echo "    # then: sudo docker logs -f qe-agent-backend"
echo "========================================================"
