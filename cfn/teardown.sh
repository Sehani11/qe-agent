#!/usr/bin/env bash
# =============================================================================
# qe-agent — Full Teardown
#
# Removes ALL AWS resources: ECR images, SSM parameters, Amplify app, and
# the CloudFormation stack (VPC, EC2, EIP, ECR repo, IAM role).
#
# Run from the project root: bash cfn/teardown.sh
# =============================================================================
set -e
sed -i 's/\r//' "$0" 2>/dev/null || true
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'
export AWS_PAGER=""

STACK_NAME="qe-agent"
REGION="ap-south-1"
IAM_USER="qe-agent-deploy"

ACCOUNT=$(aws sts get-caller-identity --query Account --output text 2>/dev/null || echo "")
if [ -z "$ACCOUNT" ]; then
  echo "ERROR: Could not determine AWS account. Is the CLI configured?"
  exit 1
fi

echo ""
echo "========================================================"
echo "  qe-agent — Full Teardown"
echo "========================================================"
echo ""
echo "  Account: $ACCOUNT  Region: $REGION"
echo "  This will permanently delete:"
echo "    - ECR repos: qe-agent/backend + qe-agent/frontend"
echo "    - SSM parameters under /qe-agent/"
echo "    - CloudFormation stack: $STACK_NAME"
echo "      (EC2, EIP, VPC, ECR repos, IAM role)"
echo "    - IAM user: $IAM_USER (incl. all access keys)"
echo "    - Local file: aws-credentials.txt"
echo ""
echo "  After this, the AWS account has NO trace of qe-agent."
echo ""
read -p "  Type 'NUKE' (case sensitive) to confirm: " CONFIRM
if [ "$CONFIRM" != "NUKE" ]; then
  echo "Aborted."
  exit 0
fi

# ── 1. Empty + delete ECR repos ─────────────────────────────────────────────
# Force-delete before the CFN stack delete so it runs faster with no races.
echo ""
echo "=== Step 1: ECR repos ==="
for REPO in qe-agent/backend qe-agent/frontend; do
  if aws ecr describe-repositories --repository-names "$REPO" \
       --region "$REGION" >/dev/null 2>&1; then
    aws ecr delete-repository --repository-name "$REPO" \
      --region "$REGION" --force >/dev/null
    echo "  Deleted $REPO."
  else
    echo "  $REPO not found — skipping."
  fi
done

# ── 3. Delete SSM parameters ─────────────────────────────────────────────────
echo ""
echo "=== Step 3: SSM parameters under /qe-agent/ ==="
NAMES=$(aws ssm describe-parameters --region "$REGION" \
  --parameter-filters "Key=Name,Option=BeginsWith,Values=/qe-agent/" \
  --query 'Parameters[].Name' --output text 2>/dev/null || echo "")
if [ -n "$NAMES" ]; then
  # describe-parameters returns tab-separated; delete-parameters takes 10 at a time
  echo "$NAMES" | tr '\t' '\n' | xargs -n10 -I {} bash -c '
    aws ssm delete-parameters --names {} --region "'"$REGION"'" >/dev/null 2>&1 || true
  '
  echo "  Deleted all /qe-agent/* parameters."
else
  echo "  No /qe-agent/* parameters — skipping."
fi

# ── 4. Delete CloudFormation stack ───────────────────────────────────────────
echo ""
echo "=== Step 4: CloudFormation stack ==="
STACK_STATUS=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" --region "$REGION" \
  --query 'Stacks[0].StackStatus' --output text 2>/dev/null || echo "DOES_NOT_EXIST")

if [ "$STACK_STATUS" != "DOES_NOT_EXIST" ]; then
  echo "  Deleting stack $STACK_NAME..."
  aws cloudformation delete-stack --stack-name "$STACK_NAME" --region "$REGION"
  aws cloudformation wait stack-delete-complete \
    --stack-name "$STACK_NAME" --region "$REGION" 2>/dev/null || true

  FINAL_STATUS=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" --region "$REGION" \
    --query 'Stacks[0].StackStatus' --output text 2>/dev/null || echo "DOES_NOT_EXIST")
  if [ "$FINAL_STATUS" = "DELETE_FAILED" ]; then
    echo "  DELETE_FAILED — retaining stuck resources and retrying..."
    STUCK=$(aws cloudformation describe-stack-events \
      --stack-name "$STACK_NAME" --region "$REGION" \
      --query "StackEvents[?ResourceStatus=='DELETE_FAILED'].LogicalResourceId" \
      --output text 2>/dev/null | tr '\t' ' ')
    aws cloudformation delete-stack --stack-name "$STACK_NAME" --region "$REGION" \
      --retain-resources $STUCK
    aws cloudformation wait stack-delete-complete \
      --stack-name "$STACK_NAME" --region "$REGION" 2>/dev/null || true
  fi
  echo "  Stack deleted."
else
  echo "  Stack not found — skipping."
fi

# ── 5. Delete CloudWatch log groups ──────────────────────────────────────────
echo ""
echo "=== Step 5: CloudWatch log groups ==="
LOG_GROUPS=$(aws logs describe-log-groups --region "$REGION" \
  --log-group-name-prefix "/aws/qe-agent" \
  --query 'logGroups[].logGroupName' --output text 2>/dev/null || echo "")
for LG in $LOG_GROUPS; do
  aws logs delete-log-group --log-group-name "$LG" --region "$REGION" >/dev/null 2>&1 || true
  echo "    Deleted $LG"
done
[ -z "$LOG_GROUPS" ] && echo "  No qe-agent log groups — skipping."

# ── 6. Delete IAM user ───────────────────────────────────────────────────────
echo ""
echo "=== Step 6: IAM user $IAM_USER ==="
if aws iam get-user --user-name "$IAM_USER" >/dev/null 2>&1; then
  # Detach all managed policies
  for POL in $(aws iam list-attached-user-policies --user-name "$IAM_USER" \
                 --query 'AttachedPolicies[].PolicyArn' --output text); do
    aws iam detach-user-policy --user-name "$IAM_USER" --policy-arn "$POL" >/dev/null 2>&1 || true
  done
  # Delete inline policies
  for POL in $(aws iam list-user-policies --user-name "$IAM_USER" \
                 --query 'PolicyNames[]' --output text); do
    aws iam delete-user-policy --user-name "$IAM_USER" --policy-name "$POL" >/dev/null 2>&1 || true
  done
  # Delete access keys
  for K in $(aws iam list-access-keys --user-name "$IAM_USER" \
               --query 'AccessKeyMetadata[].AccessKeyId' --output text); do
    aws iam delete-access-key --user-name "$IAM_USER" --access-key-id "$K" >/dev/null 2>&1 || true
  done
  aws iam delete-user --user-name "$IAM_USER" >/dev/null 2>&1 || true
  echo "  Deleted IAM user."
else
  echo "  IAM user not found — skipping."
fi

# ── 7. Local cleanup ─────────────────────────────────────────────────────────
echo ""
echo "=== Step 7: Local cleanup ==="
rm -f aws-credentials.txt
echo "  Removed aws-credentials.txt."

echo ""
echo "========================================================"
echo "  Teardown complete."
echo "========================================================"
echo ""
echo "  Verify with: bash cfn/verify-teardown.sh"
echo ""
echo "  Note: AWS_CLI_* keys in .env are now invalid. To redeploy you'll"
echo "        need a fresh IAM key (see HOW_TO_DEPLOY.md → 'Redeploy after"
echo "        a nuke')."
echo "========================================================"
