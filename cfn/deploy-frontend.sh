#!/usr/bin/env bash
# =============================================================================
# qe-agent — Frontend Deploy (EC2, Docker)
#
# Builds the Next.js Docker image with NEXT_PUBLIC_* vars baked in, pushes
# it to ECR, then triggers /opt/qe-agent/run-frontend.sh on the EC2 via
# SSM Run Command to pull the new image and restart the container.
#
# Run from the project root: bash cfn/deploy-frontend.sh
# =============================================================================
set -e
sed -i 's/\r//' "$0" 2>/dev/null || true
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'

REGION="ap-south-1"
STACK_NAME="qe-agent"
REPO_NAME="qe-agent/frontend"
ENV_FILE="$(dirname "$0")/../.env"

echo ""
echo "============================================================"
echo "  qe-agent — Frontend Deploy (EC2)"
echo "============================================================"

# ── Helper ───────────────────────────────────────────────────────────────────
read_env() {
  local key="$1"
  [ -f "$ENV_FILE" ] || { echo ""; return; }
  grep -E "^${key}=" "$ENV_FILE" | head -1 | cut -d'=' -f2- | tr -d '\r'
}

read_env_any() {
  for key in "$@"; do
    local value
    value="$(read_env "$key")"
    if [ -n "$value" ]; then
      echo "$value"
      return
    fi
  done
  echo ""
}

# ── 0. Prerequisites ─────────────────────────────────────────────────────────
if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
  echo "ERROR: Docker is not running. Start Docker Desktop and retry."
  exit 1
fi

STACK_STATUS=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" --region "$REGION" \
  --query 'Stacks[0].StackStatus' --output text 2>/dev/null || echo "")
if [ -z "$STACK_STATUS" ] || [[ "$STACK_STATUS" == *"FAILED"* ]] || [[ "$STACK_STATUS" == *"ROLLBACK"* ]]; then
  echo "ERROR: CFN stack '$STACK_NAME' missing or in bad state. Run cfn/setup-infra.sh first."
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
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
REGISTRY="$ACCOUNT.dkr.ecr.$REGION.amazonaws.com"

echo "  Instance ID  : $INSTANCE_ID"
echo "  Site URL     : $SITE_URL"
echo "  API URL      : $SITE_URL  (CloudFront routes /api/* to the backend)"

# ── 2. Build frontend image ───────────────────────────────────────────────────
echo ""
echo "=== Step 2: Building frontend image ==="
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "$REGISTRY"

IMAGE_TAG="$REGISTRY/$REPO_NAME:latest"

NEXT_PUBLIC_SUPABASE_URL_VALUE="$(read_env_any NEXT_PUBLIC_SUPABASE_URL SUPABASE_URL)"
NEXT_PUBLIC_SUPABASE_ANON_KEY_VALUE="$(read_env_any NEXT_PUBLIC_SUPABASE_ANON_KEY SUPABASE_ANON_KEY)"

docker build --platform linux/amd64 \
  -t "$IMAGE_TAG" \
  --build-arg NEXT_PUBLIC_API_URL="$SITE_URL" \
  --build-arg NEXT_PUBLIC_SUPABASE_URL="$NEXT_PUBLIC_SUPABASE_URL_VALUE" \
  --build-arg NEXT_PUBLIC_SUPABASE_ANON_KEY="$NEXT_PUBLIC_SUPABASE_ANON_KEY_VALUE" \
  --build-arg NEXT_PUBLIC_SITE_URL="$SITE_URL" \
  --build-arg NEXT_PUBLIC_AVAILABLE_MODELS="$(read_env NEXT_PUBLIC_AVAILABLE_MODELS)" \
  --build-arg NEXT_PUBLIC_DEFAULT_MODEL="$(read_env NEXT_PUBLIC_DEFAULT_MODEL)" \
  ./frontend

if [ -z "$NEXT_PUBLIC_SUPABASE_URL_VALUE" ] || [ -z "$NEXT_PUBLIC_SUPABASE_ANON_KEY_VALUE" ]; then
  echo "  WARN: One or more frontend Supabase env vars are empty."
  echo "        Checked NEXT_PUBLIC_SUPABASE_URL/SUPABASE_URL and"
  echo "        NEXT_PUBLIC_SUPABASE_ANON_KEY/SUPABASE_ANON_KEY in .env."
fi

# ── 3. Push to ECR ───────────────────────────────────────────────────────────
echo ""
echo "=== Step 3: Pushing to ECR ==="
docker push "$IMAGE_TAG"
echo "  Pushed $IMAGE_TAG"
docker image prune -af >/dev/null 2>&1 || true

# ── 4. Trigger redeploy on EC2 ────────────────────────────────────────────────
echo ""
echo "=== Step 4: Triggering frontend redeploy on EC2 ==="
CMD_ID=$(aws ssm send-command \
  --region "$REGION" \
  --instance-ids "$INSTANCE_ID" \
  --document-name "AWS-RunShellScript" \
  --comment "qe-agent frontend deploy" \
  --parameters 'commands=["/opt/qe-agent/run-frontend.sh 2>&1"]' \
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
        --command-id "$CMD_ID" --instance-id "$INSTANCE_ID" \
        --region "$REGION" --query 'StandardErrorContent' --output text
      exit 1
      ;;
    *)
      printf "  attempt %s/60 — %s\r" "$i" "$STATUS"
      sleep 5
      ;;
  esac
done
echo ""

# ── 5. Smoke test ────────────────────────────────────────────────────────────
# Hit CloudFront directly. Note: on first stack create, CloudFront takes ~10
# minutes to propagate to edge locations, so this may 502/403 right after a
# fresh `setup-infra.sh`. The container itself is healthy regardless.
echo ""
echo "=== Step 5: Smoke test ==="
for i in $(seq 1 12); do
  HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 \
    "$SITE_URL" 2>/dev/null || echo "000")
  if [ "$HTTP_CODE" = "200" ]; then
    echo "  Frontend responded 200 ✓"
    break
  fi
  echo "  attempt $i/12 — got HTTP $HTTP_CODE, waiting 10s..."
  sleep 10
done

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "========================================================"
echo "  Frontend deployed!"
echo "========================================================"
echo ""
echo "  Site URL : $SITE_URL"
echo "  API      : $SITE_URL/api/v1/..."
echo "========================================================"
