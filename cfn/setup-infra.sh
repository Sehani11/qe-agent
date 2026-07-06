#!/usr/bin/env bash
# =============================================================================
# qe-agent — Infrastructure Setup (one-time per AWS account)
#
# Provisions everything that lives OUTSIDE the application code:
#   A. IAM user 'qe-agent-deploy' (saved to .env + aws-credentials.txt)
#   B. SSM Parameter Store entries: backend env vars from .env → /qe-agent/env/*
#   C. Cleans any previous failed CFN stack
#   D. Deploys the CloudFormation stack:
#        VPC + public subnet, EC2 (t3.small) + Elastic IP, ECR repo,
#        IAM role for EC2 (ECR pull + SSM read + Session Manager)
#
# After this finishes, run `bash cfn/deploy.sh` to push the backend image and
# deploy the frontend to Amplify.
#
# Re-running is safe (idempotent). Re-syncs SSM secrets from .env every run,
# so to rotate a secret: edit .env and re-run this script.
#
# Run from the project root: bash cfn/setup-infra.sh
# =============================================================================
set -e
sed -i 's/\r//' "$0" 2>/dev/null || true
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'
unset AWS_SESSION_TOKEN

REGION="ap-south-1"
IAM_USER="qe-agent-deploy"
STACK_NAME="qe-agent"
ENV="prod"
ENV_FILE="$(dirname "$0")/../.env"

# Backend env vars to copy from .env → /qe-agent/env/* in SSM (SecureString).
# These get loaded into the Docker container at start time on the EC2.
BACKEND_ENV_KEYS=(
  DEV_USER_ID
  LLM_PROVIDER
  LLM_API_KEY
  OLLAMA_BASE_URL
  OLLAMA_MODEL
  BDD_MODEL_PROVIDER
  FINE_TUNED_MODEL_ENDPOINT
  FINE_TUNED_MODEL_API_KEY
  DATABASE_URL
  PINECONE_API_KEY
  PINECONE_INDEX_NAME
  JIRA_BASE_URL
  JIRA_API_TOKEN
  JIRA_USER_EMAIL
  GITHUB_ACCESS_TOKEN
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY
  SUPABASE_ANON_KEY
  CONFLUENCE_BASE_URL
  CONFLUENCE_API_TOKEN
  CONFLUENCE_USER_EMAIL
  DEBUG
  DISABLE_DOCS
)
# Note: CORS_ALLOW_ORIGINS is intentionally not synced from .env — it's
# computed from the live EIP by deploy-backend.sh, so it always matches
# whatever public IP CFN gave us.

echo ""
echo "============================================================"
echo "  qe-agent — Infrastructure Setup"
echo "============================================================"

if [ ! -f "$ENV_FILE" ]; then
  echo "ERROR: $ENV_FILE not found. Copy .env.example to .env and fill in values."
  exit 1
fi

# ── A. Create IAM user ────────────────────────────────────────────────────────
echo ""
echo "=== A: IAM user: $IAM_USER ==="
aws iam create-user --user-name "$IAM_USER" 2>/dev/null \
  && echo "  Created." \
  || echo "  Already exists — continuing."

aws iam attach-user-policy \
  --user-name "$IAM_USER" \
  --policy-arn arn:aws:iam::aws:policy/AdministratorAccess
echo "  AdministratorAccess attached."

# ── A2. Create access key ─────────────────────────────────────────────────────
echo ""
echo "=== A2: Access key for $IAM_USER ==="
KEY_COUNT=$(aws iam list-access-keys --user-name "$IAM_USER" \
  --query 'length(AccessKeyMetadata)' --output text)
if [ "$KEY_COUNT" -ge 2 ]; then
  echo "  2 keys exist (max) — deleting oldest."
  OLD_KEY=$(aws iam list-access-keys --user-name "$IAM_USER" \
    --query 'AccessKeyMetadata[0].AccessKeyId' --output text)
  aws iam delete-access-key --user-name "$IAM_USER" --access-key-id "$OLD_KEY"
fi

KEY_DATA=$(aws iam create-access-key --user-name "$IAM_USER" \
  --query '[AccessKey.AccessKeyId, AccessKey.SecretAccessKey]' \
  --output text)
ACCESS_KEY_ID=$(echo "$KEY_DATA" | cut -f1)
SECRET_ACCESS_KEY=$(echo "$KEY_DATA" | cut -f2)
echo "  Created: $ACCESS_KEY_ID"

# Update .env so cfn/configure-aws.sh always reflects the live key.
if grep -q '^AWS_CLI_ACCESS_KEY_ID=' "$ENV_FILE"; then
  sed -i "s|^AWS_CLI_ACCESS_KEY_ID=.*|AWS_CLI_ACCESS_KEY_ID=$ACCESS_KEY_ID|"           "$ENV_FILE"
  sed -i "s|^AWS_CLI_SECRET_ACCESS_KEY=.*|AWS_CLI_SECRET_ACCESS_KEY=$SECRET_ACCESS_KEY|" "$ENV_FILE"
else
  printf '\n# AWS CLI credentials (managed by cfn/setup-infra.sh)\n' >> "$ENV_FILE"
  printf 'AWS_CLI_ACCESS_KEY_ID=%s\n'     "$ACCESS_KEY_ID"     >> "$ENV_FILE"
  printf 'AWS_CLI_SECRET_ACCESS_KEY=%s\n' "$SECRET_ACCESS_KEY" >> "$ENV_FILE"
  printf 'AWS_CLI_REGION=%s\n'            "$REGION"            >> "$ENV_FILE"
  printf 'AWS_CLI_OUTPUT=json\n'                               >> "$ENV_FILE"
fi
echo "  Wrote keys to .env."

cat > aws-credentials.txt << EOF
# qe-agent deploy credentials (also written to .env)
AWS_ACCESS_KEY_ID=$ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY=$SECRET_ACCESS_KEY
AWS_DEFAULT_REGION=$REGION
EOF

# Switch this shell to the new user so subsequent calls use it.
unset AWS_SESSION_TOKEN
export AWS_ACCESS_KEY_ID="$ACCESS_KEY_ID"
export AWS_SECRET_ACCESS_KEY="$SECRET_ACCESS_KEY"
export AWS_DEFAULT_REGION="$REGION"

echo "  Waiting for IAM key to propagate..."
for i in $(seq 1 12); do
  CALLER=$(aws sts get-caller-identity --query Arn --output text 2>/dev/null || true)
  if [ -n "$CALLER" ]; then
    echo "  Now: $CALLER"
    break
  fi
  echo "  attempt $i/12 — waiting 5s..."
  sleep 5
  if [ "$i" -eq 12 ]; then
    echo "ERROR: IAM key did not become active after 60s."
    exit 1
  fi
done

# ── B. Sync backend env vars to SSM Parameter Store ──────────────────────────
# All non-AWS-CLI keys from .env land under /qe-agent/env/* as SecureString.
# The EC2 instance reads these at container start (see UserData in qe-agent.yaml).
echo ""
echo "=== B: Syncing backend env to SSM (/qe-agent/env/*) ==="

read_env() {
  local key="$1"
  [ -f "$ENV_FILE" ] || { echo ""; return; }
  grep -E "^${key}=" "$ENV_FILE" | head -1 | cut -d'=' -f2- | tr -d '\r'
}

put_ssm() {
  local name="$1"; local value="$2"
  if [ -z "$value" ]; then
    # Delete the parameter if it exists but the .env value was cleared.
    aws ssm delete-parameter --name "$name" --region "$REGION" >/dev/null 2>&1 || true
    echo "  $name — empty, skipped."
    return
  fi
  aws ssm put-parameter \
    --name "$name" \
    --value "$value" \
    --type SecureString \
    --overwrite \
    --region "$REGION" >/dev/null
  echo "  $name stored."
}

for KEY in "${BACKEND_ENV_KEYS[@]}"; do
  put_ssm "/qe-agent/env/$KEY" "$(read_env "$KEY")"
done

# ── C. Clean up failed previous stack ────────────────────────────────────────
echo ""
echo "=== C: Checking for previous failed CFN stack ==="
STACK_STATUS=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" --region "$REGION" \
  --query "Stacks[0].StackStatus" --output text 2>/dev/null || echo "DOES_NOT_EXIST")

if [[ "$STACK_STATUS" == *"FAILED"* ]] || [[ "$STACK_STATUS" == *"ROLLBACK_COMPLETE"* ]]; then
  echo "  Stack in $STACK_STATUS — deleting first..."

  # Force-empty the ECR repo (CFN can't delete a repo with images even with EmptyOnDelete
  # if the stack is mid-rollback).
  if aws ecr describe-repositories --repository-names "qe-agent/backend" \
       --region "$REGION" >/dev/null 2>&1; then
    aws ecr delete-repository --repository-name "qe-agent/backend" \
      --region "$REGION" --force >/dev/null 2>&1 || true
    echo "    Force-deleted ECR repo qe-agent/backend."
  fi

  aws cloudformation delete-stack --stack-name "$STACK_NAME" --region "$REGION"
  aws cloudformation wait stack-delete-complete --stack-name "$STACK_NAME" --region "$REGION" 2>/dev/null || true
  echo "  Old stack deleted."
elif [ "$STACK_STATUS" = "DOES_NOT_EXIST" ]; then
  echo "  No existing stack — fresh deploy."
else
  echo "  Stack exists in $STACK_STATUS — proceeding with update."
fi

# ── D. Deploy CloudFormation stack ───────────────────────────────────────────
# If the EC2 instance already exists, pin LatestAmiId to the AMI currently
# running. Without this, the dynamic SSM AMI parameter may resolve to a newer
# AMI than was used at creation, which would trigger an instance replacement —
# destroying the running containers and any in-place state.
echo ""
echo "=== D: Deploying CloudFormation stack ($STACK_NAME) ==="
AMI_ID=""
if [ "$STACK_STATUS" != "DOES_NOT_EXIST" ]; then
  # Stack already exists — pin to the running instance's AMI so CFN doesn't
  # try to replace the EC2 just because the latest AMI changed.
  EXISTING_INSTANCE_ID=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='BackendInstanceId'].OutputValue" \
    --output text 2>/dev/null || echo "")
  if [ -n "$EXISTING_INSTANCE_ID" ] && [ "$EXISTING_INSTANCE_ID" != "None" ]; then
    AMI_ID=$(aws ec2 describe-instances \
      --instance-ids "$EXISTING_INSTANCE_ID" --region "$REGION" \
      --query 'Reservations[0].Instances[0].ImageId' --output text 2>/dev/null || echo "")
    echo "  Pinning LatestAmiId=$AMI_ID (running instance AMI — prevents replacement)."
  fi
fi

if [ -z "$AMI_ID" ]; then
  # Fresh deploy (or couldn't read current AMI) — resolve the latest AL2023 AMI.
  AMI_ID=$(aws ssm get-parameter \
    --name "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64" \
    --region "$REGION" --query 'Parameter.Value' --output text)
  echo "  Resolved latest AL2023 AMI: $AMI_ID"
fi

aws cloudformation deploy \
  --stack-name "$STACK_NAME" \
  --template-file cfn/qe-agent.yaml \
  --capabilities CAPABILITY_NAMED_IAM \
  --region "$REGION" \
  --disable-rollback \
  --parameter-overrides \
      Env="$ENV" \
      InstanceType="t3.small" \
      LatestAmiId="$AMI_ID"
echo "  Stack deployed."

# ── Print outputs ────────────────────────────────────────────────────────────
echo ""
echo "============================================================"
echo "  Infrastructure ready. Stack outputs:"
echo "============================================================"

INSTANCE_ID=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='BackendInstanceId'].OutputValue" --output text)
PUBLIC_IP=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='BackendPublicIp'].OutputValue" --output text)
API_URL=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='BackendApiUrl'].OutputValue" --output text)
ECR_REGISTRY=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='EcrRegistry'].OutputValue" --output text)

echo ""
echo "  EC2 Instance ID : $INSTANCE_ID"
echo "  Public IP (EIP) : $PUBLIC_IP"
echo "  API URL         : $API_URL"
echo "  ECR Registry    : $ECR_REGISTRY"
echo ""
echo "  Open a shell on the instance (no SSH needed):"
echo "    aws ssm start-session --target $INSTANCE_ID --region $REGION"
echo ""
echo "  Next: deploy backend image + frontend:"
echo "    bash cfn/deploy.sh"
echo "============================================================"
