#!/usr/bin/env bash
# Run from project root: bash cfn/configure-aws.sh
set -e

ENV_FILE="$(dirname "$0")/../.env"

if [ ! -f "$ENV_FILE" ]; then
  echo "ERROR: .env file not found at $ENV_FILE"
  exit 1
fi

# Load only the AWS_CLI_* vars from .env (ignore comments and blank lines)
while IFS='=' read -r key value; do
  [[ "$key" =~ ^#.*$ || -z "$key" ]] && continue
  case "$key" in
    AWS_CLI_ACCESS_KEY_ID)    ACCESS_KEY_ID="${value//$'\r'/}" ;;
    AWS_CLI_SECRET_ACCESS_KEY) SECRET_KEY="${value//$'\r'/}" ;;
    AWS_CLI_REGION)           REGION="${value//$'\r'/}" ;;
    AWS_CLI_OUTPUT)           OUTPUT="${value//$'\r'/}" ;;
  esac
done < "$ENV_FILE"

if [ -z "$ACCESS_KEY_ID" ] || [ -z "$SECRET_KEY" ]; then
  echo "ERROR: AWS_CLI_ACCESS_KEY_ID or AWS_CLI_SECRET_ACCESS_KEY not set in .env"
  exit 1
fi

aws configure set aws_access_key_id     "$ACCESS_KEY_ID"
aws configure set aws_secret_access_key "$SECRET_KEY"
aws configure set region                "${REGION:-ap-south-1}"
aws configure set output                "${OUTPUT:-json}"

echo "AWS CLI configured."
echo ""
aws sts get-caller-identity
