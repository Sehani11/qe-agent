# qe-agent — Deploy Guide (EC2)

End-to-end guide for deploying qe-agent to AWS from a Windows + Git Bash environment.

## What gets created

| Resource | Service |
|---|---|
| 1× t3.small EC2 + 20 GB gp3 | EC2 + EBS |
| 1× Elastic IP (always allocated) | VPC |
| 2× ECR repositories (backend + frontend images) | ECR |
| 1× SSM Parameter Store (SecureStrings) | SSM |
| VPC + 1 public subnet + IGW + SG | VPC |

> Database (Supabase Postgres), Pinecone, Jira, GitHub, Confluence, and the LLM provider are external services — not part of this stack. Their credentials live in `.env` and are mirrored into SSM by the setup script.

---

## Prerequisites

- **AWS CLI v2** ([download for Windows](https://awscli.amazonaws.com/AWSCLIV2.msi))
- **Docker Desktop** (must be running before any deploy script)
- **Git Bash** (comes with Git for Windows)
- An AWS account with permissions to create IAM users (root or admin user for first-time bootstrap)

> The project includes a `.gitattributes` that should keep LF line endings on shell scripts. If you see `Parameter name must be a fully qualified name` or similar weirdness, run `sed -i 's/\r//' cfn/*.sh`.

---

## Step 1 — Install AWS CLI

Run `AWSCLIV2.msi`, then in Git Bash:

```bash
export PATH="$PATH:/c/Program Files/Amazon/AWSCLIV2"
echo 'export PATH="$PATH:/c/Program Files/Amazon/AWSCLIV2"' >> ~/.bashrc
aws --version       # aws-cli/2.x.x ...
```

## Step 2 — Get an IAM access key

If this is the first time deploying to this AWS account, create an IAM user with `AdministratorAccess` (Console → IAM → Users → Create user). Copy the access key ID and secret.

Open the project's root `.env` and add the AWS keys (the rest of the file should already have your service tokens — see `.env.example`):

```
AWS_CLI_ACCESS_KEY_ID=AKIA...
AWS_CLI_SECRET_ACCESS_KEY=...
AWS_CLI_REGION=ap-south-1
AWS_CLI_OUTPUT=json
```

Then run:

```bash
bash cfn/configure-aws.sh
```

This populates `~/.aws/credentials` + `~/.aws/config` and prints `aws sts get-caller-identity` to confirm.

## Step 3 — Start Docker Desktop

Wait until it shows **Engine running**.

```bash
docker info     # should print server info, not an error
```

## Step 5 — Deploy

The deploy is split into separate scripts so you only re-run what changed:

```bash
# 1. One-time AWS setup + EC2/VPC/ECR
bash cfn/setup-infra.sh

# 2. Build & push backend image, deploy frontend to Amplify
bash cfn/deploy.sh
```

Or use the all-in-one wrapper for first-time deploys:

```bash
bash cfn/bootstrap.sh    # = setup-infra.sh + deploy.sh
```

### When to use which script

| Script | Run it when… |
|---|---|
| `cfn/setup-infra.sh` | First deploy, or after editing `qe-agent.yaml`, or to rotate the `.env` → SSM secrets |
| `cfn/deploy-backend.sh` | After any change in `backend/` |
| `cfn/deploy-frontend.sh` | After any change in `frontend/` |
| `cfn/deploy.sh` | Both at once |
| `cfn/bootstrap.sh` | First-time deploy (just calls setup-infra + deploy) |

### `cfn/setup-infra.sh` — what it does (idempotent)

| Phase | What happens |
|---|---|
| A | Creates IAM user `qe-agent-deploy` (skipped if exists), attaches AdminAccess |
| A2 | Creates new access key, writes to `aws-credentials.txt` + `.env` |
| B | Mirrors backend env vars from `.env` → `/qe-agent/env/*` (SSM SecureString) |
| C | If a previous CFN stack is `ROLLBACK_COMPLETE` or `DELETE_FAILED`, deletes it first |
| D | Deploys the CloudFormation stack: VPC, subnet, EC2 + Elastic IP, ECR repo, IAM role |

### `cfn/deploy-backend.sh` — what it does

| Step | What it does |
|---|---|
| 1 | Reads stack outputs (instance ID, public IP, ECR registry) |
| 2 | Builds the backend Docker image (`--platform linux/amd64`) and pushes to ECR |
| 3 | Triggers `/opt/qe-agent/run.sh` on the EC2 via SSM Run Command — pulls new image, refreshes env from SSM, restarts container |
| 4 | Smoke-tests `http://<elastic-ip>/health` (or `/docs`) |

### `cfn/deploy-frontend.sh` — what it does

| Step | What it does |
|---|---|
| 1 | Reads `BackendApiUrl` + `BackendPublicIp` from CFN outputs, Supabase vars from `.env` |
| 2 | Builds the Next.js Docker image (`--platform linux/amd64`) with `NEXT_PUBLIC_*` vars baked in via `--build-arg` |
| 3 | Pushes the image to ECR (`qe-agent/frontend:latest`) |
| 4 | Triggers `/opt/qe-agent/run-frontend.sh` on the EC2 via SSM Run Command — pulls new image, restarts container on port 80 |
| 5 | Smoke-tests `http://<elastic-ip>` |

---

## How env vars flow

| Type | Example | Source | Lands in |
|---|---|---|---|
| AWS CLI creds | `AWS_CLI_ACCESS_KEY_ID` | root `.env` | `~/.aws/credentials` (your laptop only) |
| Frontend (`NEXT_PUBLIC_*`) | `NEXT_PUBLIC_API_URL` | CFN output + `.env` → `--build-arg` at `docker build` | baked into the Next.js bundle inside the Docker image |
| Backend env | `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `SUPABASE_*`, etc. | root `.env` → SSM `/qe-agent/env/*` (SecureString) | loaded by `/opt/qe-agent/run-backend.sh` at container start |

The deployed backend container **never sees your local `.env`** directly — it reads from SSM at start. Local and cloud are kept fully separate.

To **rotate a secret**: edit `.env`, re-run `bash cfn/setup-infra.sh` (re-syncs SSM), then `bash cfn/deploy-backend.sh` (restarts the container with the new env).

---

## Useful commands after deployment

```bash
# Open a shell on the EC2 instance (no SSH key needed — uses SSM Session Manager)
INSTANCE_ID=$(aws cloudformation describe-stacks --stack-name qe-agent \
  --region ap-south-1 \
  --query "Stacks[0].Outputs[?OutputKey=='BackendInstanceId'].OutputValue" \
  --output text)
aws ssm start-session --target "$INSTANCE_ID" --region ap-south-1

# Once on the instance
sudo docker ps                              # is the backend container running?
sudo docker logs -f qe-agent-backend        # tail logs
sudo /opt/qe-agent/run.sh                   # force a redeploy (pulls latest image)

# Get the public API URL
aws cloudformation describe-stacks --stack-name qe-agent --region ap-south-1 \
  --query "Stacks[0].Outputs[?OutputKey=='BackendApiUrl'].OutputValue" --output text

# Stop the instance (keeps EIP + EBS allocated, no compute charges)
INSTANCE_ID=$(aws cloudformation describe-stacks --stack-name qe-agent \
  --region ap-south-1 \
  --query "Stacks[0].Outputs[?OutputKey=='BackendInstanceId'].OutputValue" \
  --output text)
aws ec2 stop-instances  --instance-ids "$INSTANCE_ID" --region ap-south-1
aws ec2 start-instances --instance-ids "$INSTANCE_ID" --region ap-south-1
```

> The Elastic IP keeps the same public IP across stop/start — your Amplify frontend's `NEXT_PUBLIC_API_URL` stays valid.

---

## Cost-saving strategy

| Mode | Data preserved? |
|---|---|
| **Fully running** | yes |
| **EC2 stopped** (EIP + EBS still allocated) | yes |
| **`cfn/teardown.sh`** | SSM secrets are wiped, but `.env` still has them — re-run setup-infra.sh restores |

For demos: deploy once with `bootstrap.sh`, stop the instance between sessions, start it before each demo.

For long breaks: full teardown is cheapest. Re-running `bootstrap.sh` will pull from your `.env` again.

---

## Frontend alternatives

### Option A — Vercel

If AWS-only isn't a hard requirement, Vercel handles Next.js SSR with zero config. Outside the scope of this guide.

---

## Adding a new backend env var

1. Add it to `.env` and `.env.example`.
2. Add the key name to `BACKEND_ENV_KEYS` in `cfn/setup-infra.sh`.
3. Re-run `bash cfn/setup-infra.sh` (syncs SSM).
4. Re-run `bash cfn/deploy-backend.sh` (restarts container with new env).

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `aws: command not found` | Add `/c/Program Files/Amazon/AWSCLIV2` to PATH (Step 1) |
| `Invalid value for key: newline/carriage return` | `sed -i 's/\r//' cfn/*.sh` — VS Code added CRLFs |
| `Parameter name must be a fully qualified name` (SSM) | MSYS path conversion — scripts now `export MSYS_NO_PATHCONV=1`, but if it still happens run `sed -i 's/\r//' cfn/*.sh` |
| `InvalidClientTokenId` from any AWS call | The IAM access key in `.env` is dead. Either re-create one via Console → IAM → Users → qe-agent-deploy → Security credentials, paste into `.env`, then `bash cfn/configure-aws.sh`; or use root creds and re-run `bash cfn/setup-infra.sh` (it will rotate the key) |
| `frontend/out/ was not created` after `npm run build` | Add `output: "export"` to `next.config.ts` — see Step 3 |
| Server Components error during `npm run build` | You have code that needs SSR. Either convert to client components (`"use client"`) or use [Option B](#option-b--amplify-with-git-connection-full-nextjs-compute) |
| Smoke test fails — API returns 502 / no response | SSH onto the instance (`aws ssm start-session ...`), check `sudo docker ps` and `sudo docker logs qe-agent-backend`. Most often: missing env var → app crash on startup. Check `cat /opt/qe-agent/.env` |
| `An error occurred (ParameterNotFound)` during run.sh | A required SSM key wasn't synced. Edit `.env`, re-run `bash cfn/setup-infra.sh`, then `bash cfn/deploy-backend.sh` |
| Image runs but with `exec format error` in the container | Image was built for arm64. `deploy-backend.sh` already passes `--platform linux/amd64` — re-run it from a fresh shell |
| Browser shows old JS after redeploy | Hard refresh: Ctrl+Shift+R (Amplify caches aggressively) |
| Mixed-content error in browser console | Amplify is HTTPS, EC2 is HTTP. Fix: put a CloudFront distribution in front of the EC2 (custom: not in this template), or add a custom domain + ACM cert. For demos the browser will warn but allow API calls if both are http (use the http://< amplifyapp.com>... URL — or just the EIP directly) |

---

## Teardown — `NUKE` (delete everything)

```bash
bash cfn/teardown.sh
```

Type `NUKE` (case sensitive) to confirm.

| # | What gets deleted |
|---|---|
| 1 | Amplify app `qe-agent-frontend` |
| 2 | ECR repo `qe-agent/backend` (force, with all images) |
| 3 | All `/qe-agent/*` SSM parameters |
| 4 | CloudFormation stack (EC2, EIP, VPC, ECR, IAM role) |
| 5 | CloudWatch log groups under `/aws/qe-agent` |
| 6 | IAM user `qe-agent-deploy` (incl. all access keys + policies) |
| 7 | Local `aws-credentials.txt` |

> Your `.env` file is untouched. The `AWS_CLI_*` keys in `.env` will be invalid after the nuke.

### Verify the nuke

```bash
bash cfn/verify-teardown.sh
```

Walks every resource type and prints `[OK]` or `[LEFT]`. Exits 0 if clean.

### Redeploy after a nuke

You no longer have an IAM key. Two options:

1. **AWS Console**: log in as root, IAM → Users → Create user `qe-agent-deploy` with `AdministratorAccess`, generate an access key. Paste into `.env`. Then:
   ```bash
   bash cfn/configure-aws.sh
   bash cfn/bootstrap.sh
   ```

2. **Use root creds temporarily**: put your root account's access key into `.env` `AWS_CLI_*`, run `bash cfn/configure-aws.sh`, then `bash cfn/bootstrap.sh` will create the `qe-agent-deploy` user fresh and switch to it. Remove the root key from `.env` afterwards.

---

## Script reference

| Script | Purpose |
|---|---|
| `cfn/configure-aws.sh` | Read `AWS_CLI_*` from `.env` → set up `~/.aws/credentials` |
| `cfn/setup-infra.sh` | One-time AWS infra setup: IAM user, SSM env sync, CFN stack |
| `cfn/deploy-backend.sh` | Build + push backend image to ECR, redeploy via SSM Run Command |
| `cfn/deploy-frontend.sh` | Build Next.js static export, zip, push to AWS Amplify Hosting |
| `cfn/deploy.sh` | Wrapper that runs deploy-backend + deploy-frontend |
| `cfn/bootstrap.sh` | All-in-one wrapper: setup-infra + deploy |
| `cfn/teardown.sh` | NUKE — delete everything (requires typing `NUKE`) |
| `cfn/verify-teardown.sh` | Lists any qe-agent resources still on AWS — exit 0 if clean |
| `cfn/qe-agent.yaml` | The CloudFormation template (VPC, EC2 + EIP, ECR, IAM) |
