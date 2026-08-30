# Deploying the routing agent to AWS Bedrock AgentCore Runtime

`deploy/FoodRescueRouterAgent/` is a standalone AgentCore Runtime deployment of the
same routing agent -- same tools, same system prompt, same Strands Agent -- as the
local dashboard app (`src/food_rescue_router/`). It's a separate deployable unit
because AgentCore Runtime packages an isolated app directory and expects a single
`/invocations` entrypoint, not a multi-route FastAPI app with a dashboard. The
`food_rescue_router/` subpackage inside it (`data_store.py`, `seed_data.py`,
`tools.py`) is a deployable copy of the same logic, adapted to use the container's
`/tmp` for its SQLite state instead of a path relative to the main project.

## What's here

```
deploy/FoodRescueRouterAgent/
  agentcore/               # AgentCore CLI project config + CDK stack (auto-generated)
  app/FoodRescueRouterAgent/
    main.py                 # entrypoint: wraps the Strands Agent in BedrockAgentCoreApp
    food_rescue_router/      # deployable copy of tools/data_store/seed_data
    pyproject.toml
```

## Prerequisites

- [AgentCore CLI](https://github.com/aws/agentcore-cli): `npm install -g @aws/agentcore`
- [`uv`](https://github.com/astral-sh/uv): `pip install uv` (used to manage the app's Python env)
- AWS CDK (for the actual `deploy` step): `npm install -g aws-cdk`, then `cdk bootstrap`
  once per AWS account/region
- AWS credentials with Bedrock model access and permissions to create IAM roles /
  CloudFormation stacks / S3 (see [AgentCore CLI permissions](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-permissions.html#runtime-permissions-cli))

## Test locally first (no AWS deployment yet)

```bash
cd deploy/FoodRescueRouterAgent
agentcore dev --logs          # starts a local server that mimics AgentCore Runtime
```

In another terminal:

```bash
agentcore dev "New donation offer:
- donation_id: test-1
- donor: Green Aisle Market (Downtown)
- category: produce
- quantity_lbs: 60
- pickup_window: Today 1:00pm to Today 5:00pm

Route this donation now."
```

This exercises the exact same agent + tools the real deployment will run, without
creating any AWS resources.

## Live deployment

Deployed 2026-08-30 to `us-east-1`:

- **Runtime ARN**: `arn:aws:bedrock-agentcore:us-east-1:141353495650:runtime/FoodRescueRouterAgent_FoodRescueRouterAgent-FDKqFqAfP3`
- **Stack**: `AgentCore-FoodRescueRouterAgent-default`

Verified with a real `agentcore invoke` call end to end: given a 40 lb bakery
donation, the deployed agent correctly picked the food bank with the highest
need in the same zone, noticed one candidate driver's availability window was
too tight for the pickup time, and picked a better-fitting driver instead —
then called `create_match` for real. Same reasoning quality as the local app,
running as an actual AWS-hosted service.

## Deploy for real

```bash
cd deploy/FoodRescueRouterAgent
agentcore deploy
```

This provisions real, billed AWS resources: an IAM execution role, an AgentCore
Runtime, and the CloudFormation stack / S3 staging bucket CDK needs to manage them.
Use `agentcore deploy --dry-run` first to preview what will be created.

Then:

```bash
agentcore invoke "New donation offer: ..."   # test the deployed agent
agentcore status                              # find the runtime ARN, logs, etc.
```

To tear everything down:

```bash
agentcore remove all
agentcore deploy   # applies the removal, deletes the AWS resources
```
