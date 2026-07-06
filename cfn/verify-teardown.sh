#!/usr/bin/env bash
# =============================================================================
# qe-agent — Verify Teardown
#
# Walks every resource type the project creates and prints [OK] or [LEFT].
# Exit 0 if AWS account is clean of qe-agent, 1 otherwise.
#
# Run from the project root: bash cfn/verify-teardown.sh
# =============================================================================
set -e
sed -i 's/\r//' "$0" 2>/dev/null || true
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'
export AWS_PAGER=""

REGION="ap-south-1"
STACK_NAME="qe-agent"
IAM_USER="qe-agent-deploy"

ANY_LEFT=0

mark_ok()   { echo "  [OK]   $1"; }
mark_left() { echo "  [LEFT] $1"; ANY_LEFT=1; }

echo ""
echo "============================================================"
echo "  qe-agent — Verifying teardown"
echo "============================================================"

# CloudFormation stack
STACK_STATUS=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" --region "$REGION" \
  --query 'Stacks[0].StackStatus' --output text 2>/dev/null || echo "")
if [ -z "$STACK_STATUS" ]; then
  mark_ok "CFN stack '$STACK_NAME' deleted."
else
  mark_left "CFN stack '$STACK_NAME' status: $STACK_STATUS"
fi

# ECR repos
for REPO in qe-agent/backend qe-agent/frontend; do
  if aws ecr describe-repositories --repository-names "$REPO" \
       --region "$REGION" >/dev/null 2>&1; then
    mark_left "ECR repo $REPO still exists."
  else
    mark_ok "ECR repo $REPO deleted."
  fi
done

# SSM parameters
PARAM_COUNT=$(aws ssm describe-parameters --region "$REGION" \
  --parameter-filters "Key=Name,Option=BeginsWith,Values=/qe-agent/" \
  --query 'length(Parameters)' --output text 2>/dev/null || echo "0")
if [ "$PARAM_COUNT" = "0" ]; then
  mark_ok "No /qe-agent/* SSM parameters."
else
  mark_left "$PARAM_COUNT /qe-agent/* SSM parameters still present."
fi

# Elastic IPs tagged for the project
EIP_COUNT=$(aws ec2 describe-addresses --region "$REGION" \
  --filters "Name=tag:Name,Values=qe-agent-*-eip" \
  --query 'length(Addresses)' --output text 2>/dev/null || echo "0")
if [ "$EIP_COUNT" = "0" ]; then
  mark_ok "No qe-agent Elastic IPs."
else
  mark_left "$EIP_COUNT qe-agent Elastic IPs still allocated."
fi

# EC2 instances tagged for the project (running OR stopped)
INST_COUNT=$(aws ec2 describe-instances --region "$REGION" \
  --filters "Name=tag:Project,Values=qe-agent" \
            "Name=instance-state-name,Values=pending,running,stopping,stopped" \
  --query 'length(Reservations[].Instances[])' --output text 2>/dev/null || echo "0")
if [ "$INST_COUNT" = "0" ]; then
  mark_ok "No qe-agent EC2 instances."
else
  mark_left "$INST_COUNT qe-agent EC2 instances still present."
fi

# IAM user
if aws iam get-user --user-name "$IAM_USER" >/dev/null 2>&1; then
  mark_left "IAM user $IAM_USER still exists."
else
  mark_ok "IAM user $IAM_USER deleted."
fi

# CloudFront distributions (CFN deletes these via the stack, but verify)
CF_COUNT=$(aws cloudfront list-distributions --region "$REGION" \
  --query "length(DistributionList.Items[?Comment=='qe-agent-prod' || Comment=='qe-agent-dev'])" \
  --output text 2>/dev/null || echo "0")
if [ "$CF_COUNT" = "0" ] || [ "$CF_COUNT" = "None" ]; then
  mark_ok "No qe-agent CloudFront distributions."
else
  mark_left "$CF_COUNT qe-agent CloudFront distributions still present."
fi

# CloudWatch log groups
LG_COUNT=$(aws logs describe-log-groups --region "$REGION" \
  --log-group-name-prefix "/aws/qe-agent" \
  --query 'length(logGroups)' --output text 2>/dev/null || echo "0")
if [ "$LG_COUNT" = "0" ]; then
  mark_ok "No /aws/qe-agent log groups."
else
  mark_left "$LG_COUNT /aws/qe-agent log groups still present."
fi

echo ""
if [ "$ANY_LEFT" = "0" ]; then
  echo "  All clean."
  exit 0
else
  echo "  Some resources remain — re-run cfn/teardown.sh or clean manually."
  exit 1
fi
