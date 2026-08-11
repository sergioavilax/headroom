# Headroom on AWS — the operator's runbook

Every command in dependency order, with what it costs stated **before** it runs.

**Claude Code writes this file and never executes it.** BUILD_PLAN §0.2 invariant 2: the
human runs every `terraform apply`, every `terraform destroy`, and every AWS mutation.
Two terminals — the agent in one, your hands in the other. Errors get pasted back, never
guessed around.

---

## 0. What this is

Two Terraform roots, split by **lifetime** rather than by service kind:

| Root | Holds | Lives |
|---|---|---|
| `deploy/aws/data` | VPC, RDS, DynamoDB, ECR, the secret containers | created in P9, **survives P9's teardown**, carries P10, destroyed at the end of P10 |
| `deploy/aws/compute` | ALB, both Fargate services, the rollup Lambda, the alarms, the log groups | created and destroyed inside a day, as many times as you like |

That split is what makes §P9's gate (*"destroy the same day"*) and §P10's three-day EKS
window both possible. `terraform -chdir=deploy/aws/compute destroy` touches nothing the
data root owns, and the data root's plan is empty afterwards — which step 11 checks
rather than assumes.

**State is local and gitignored.** One operator, one machine, two roots; an S3 backend
plus a lock table would be two more things to create before the first apply and two more
to destroy after the last. The consequence is worth reading twice: `terraform.tfstate` is
the only copy, so **do not `git clean -xdf` between an apply and its destroy** — you would
be left deleting resources by hand from the console.

### Prerequisites

```bash
terraform version        # >= 1.9
aws sts get-caller-identity
docker version
```

`aws sts get-caller-identity` must return *your* account. Everything below is scoped to
one region (`us-east-1` unless you change it in both tfvars files).

### What it costs

List price, `us-east-1`, one gateway task and one console task, excluding data transfer:

| Line | Rate | Per day |
|---|---|---:|
| Application Load Balancer | $0.0225/hr + LCU | **$0.68** |
| Fargate — gateway (0.5 vCPU, 2 GB) | $0.0291/hr | **$0.70** |
| RDS `db.t4g.micro` + 20 GB gp3 | $0.016/hr + $0.115/GB-mo | **$0.46** |
| Secrets Manager interface endpoint (2 AZs) | $0.01/hr per AZ | **$0.48** |
| Fargate — console (0.25 vCPU, 0.5 GB) | $0.0123/hr | **$0.30** |
| Secrets Manager, 4 secrets | $0.40/mo each | $0.05 |
| CloudWatch — 4 alarms, 3 custom metrics, logs | | $0.06 |
| Cloud Map private DNS namespace + registration | $0.50/mo + $0.10/mo | $0.02 |
| DynamoDB on-demand, ECR storage, Lambda | | $0.02 |
| **Total, compute up** | | **≈ $2.77 per day** |
| **Total, data layer alone** (after step 10) | | **≈ $0.53 per day** |

**Projected P9 total: $3–4**, against §0.6's $5–8 for this phase. The data layer then runs
at ~$0.53/day until Phase 10 destroys it, which is ~$2 across a three-day window and comes
out of §0.6's P10 line.

Two of those lines are worth arguing with before you spend them:

- **There is no NAT gateway** — it would be $1.08/day on its own, forty percent of the
  bill. The Fargate tasks run in public subnets with a public IP and no inbound rule
  except from the load balancer, which is how they reach Anthropic and ECR for nothing;
  DynamoDB goes through a free gateway endpoint.
- **The $0.48/day endpoint exists because §P9 asks for a Lambda.** The rollup runs in the
  private subnets (that is where RDS is), a VPC Lambda with no default route cannot reach
  Secrets Manager, and an interface endpoint is what fixes that. A scheduled ECS task on
  the gateway's own task definition would do the same work for free. It is a Lambda
  because DynamoDB and Lambda are the two listing gaps this project exists to close, and
  closing one with "we used a cron task instead" closes nothing.

To cut ~$0.30/day, set `ui_desired_count = 0` and read the ledger with `curl` — the
console is a client of `/admin/*` and shows you nothing the API will not.

### One stated limitation, before you rely on it

**Both listeners are HTTP.** There is no domain, so there is no ACM certificate, and the
security control is the `/32` allow-list on the load balancer's security group — which is
what §P9 asks for by name. The cost is real: the root admin token crosses your own
connection to the ALB in the clear. What production adds is a hostname, a certificate,
HTTPS listeners, and an HTTP listener that does nothing but redirect.

---

## 1. Activate the cost-allocation tags — **before anything is charged by the hour**

This is the lesson BUILD_PLAN §0.4's A7 names by hand: Backline's cost chase failed
because its resources were not tagged from the first apply. It also has a chicken-and-egg
nobody mentions — **AWS will not let you activate a tag key it has never seen on a
resource.** So create two free resources first, activate, then apply the rest.

```bash
cd deploy/aws/data
terraform init

# The two ECR repositories, and nothing else. Empty repositories cost nothing, and this
# is enough to make the four tag keys visible to Billing.
terraform apply -target=aws_ecr_repository.gateway -target=aws_ecr_repository.ui
```

*Expected:* `Apply complete! Resources: 2 added, 0 changed, 0 destroyed.`

```bash
aws ce update-cost-allocation-tags-status --cost-allocation-tags-status \
  TagKey=Project,Status=Active TagKey=Layer,Status=Active \
  TagKey=Phase,Status=Active TagKey=ManagedBy,Status=Active

aws ce list-cost-allocation-tags --status Active \
  --query 'CostAllocationTags[].{Key:TagKey,Status:Status}' --output table
```

*Expected:* four rows, all `Active`. If `update-cost-allocation-tags-status` returns
`ValidationException` naming a key, the apply above did not land — re-run it and try
again. Activation is also available in the console under **Billing → Cost allocation
tags**, and either way it takes up to 24 hours to appear in Cost Explorer.

---

## 2. Apply the data layer — **~$0.53/day starts here**

```bash
cd deploy/aws/data
terraform plan -out=data.plan
terraform apply data.plan
```

*Expected:* about 25 resources added. RDS takes 5–10 minutes; the rest is seconds.

```bash
terraform output
```

Keep this terminal. Steps 3 and 4 read from it.

---

## 3. Build and push the images — **free, but the slow step**

```bash
cd ../../..                                   # repo root
REGION=$(terraform -chdir=deploy/aws/data output -raw region)
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
GATEWAY_REPO=$(terraform -chdir=deploy/aws/data output -raw gateway_repository_url)
UI_REPO=$(terraform -chdir=deploy/aws/data output -raw ui_repository_url)

aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "$ACCOUNT.dkr.ecr.$REGION.amazonaws.com"
```

*Expected:* `Login Succeeded`.

```bash
# WITH_EMBED=1 adds the `embed` extra and bakes bge-small-en-v1.5's weights into the
# image — BUILD_PLAN L6. Measured: 3.81 GB unpacked, ~810 MiB over the wire.
docker build --build-arg WITH_EMBED=1 -t "$GATEWAY_REPO:latest" .
docker build -t "$UI_REPO:latest" ./ui

docker push "$GATEWAY_REPO:latest"            # ~810 MiB — minutes on a home upload
docker push "$UI_REPO:latest"                 # ~100 MiB
```

*Expected:* `latest: digest: sha256:… size: …` from each push.

If you would rather not push a 3.8 GB image, drop `--build-arg WITH_EMBED=1`. The gateway
still does **exact** caching and answers 503 to a request for `semantic`, naming the extra
— which is the shipped behaviour and an honest thing to demo. Semantic caching is the one
feature you lose.

---

## 4. Put the three secrets in — **by hand, with a leading space**

Terraform created the containers and no versions; the values never touch state, a tfvars
file, or this repo (BUILD_PLAN §0.2 invariant 3). **Note the leading space on each
command** — it keeps them out of shell history where `HISTCONTROL=ignorespace` is set.

First the database URL, composed from RDS's own generated password. Terraform never saw
it either:

```bash
DATA=deploy/aws/data
DB_SECRET=$(terraform -chdir=$DATA output -raw db_master_secret_arn)
DB_HOST=$(terraform -chdir=$DATA output -raw db_address)
DB_NAME=$(terraform -chdir=$DATA output -raw db_name)
DB_USER=$(terraform -chdir=$DATA output -raw db_username)

DB_PASS=$(aws secretsmanager get-secret-value --secret-id "$DB_SECRET" \
  --query SecretString --output text | jq -r .password)

# Percent-encoded: an RDS-generated password may contain `#`, `?`, or `%`, any of which
# would truncate or corrupt a URL.
DB_PASS_ENC=$(jq -rn --arg p "$DB_PASS" '$p|@uri')

 aws secretsmanager put-secret-value --secret-id headroom/database-url \
   --secret-string "postgresql://$DB_USER:$DB_PASS_ENC@$DB_HOST:5432/$DB_NAME"
```

Then the root admin token — a value you invent, and the only credential the console ever
asks for:

```bash
 aws secretsmanager put-secret-value --secret-id headroom/admin-token \
   --secret-string "$(openssl rand -base64 32)"
```

Read it back once and keep it somewhere you can paste from; you will type it into the
console in step 8:

```bash
aws secretsmanager get-secret-value --secret-id headroom/admin-token \
  --query SecretString --output text
```

And the provider key, which is the one that can spend money:

```bash
 aws secretsmanager put-secret-value --secret-id headroom/anthropic-api-key \
   --secret-string "sk-ant-…"
```

*Expected:* each returns a JSON object with a `VersionId`. **All three must have a value
before step 6** — a secret Terraform created and nobody filled in makes the task fail to
start with `ResourceNotFoundException` naming it.

---

## 5. Build the Lambda package — **free**

```bash
make lambda-build
```

*Expected:*

```
built deploy/aws/lambda/build: asyncpg, headroom
13.0 MiB unzipped · handler headroom.rollup.handler.handler
```

Terraform zips that directory itself, so there is no `zip` in the loop and the archive's
hash is what tells it to redeploy. The script refuses to finish if asyncpg landed without
its compiled protocol module — that failure is otherwise invisible until the first
connection attempt, once a night, in a VPC.

---

## 6. Apply the compute layer — **~$2.77/day starts here**

```bash
cd deploy/aws/compute
cp terraform.tfvars.example terraform.tfvars

# The one machine-specific fact. There is no default, deliberately.
echo "home_cidr = \"$(curl -s https://checkip.amazonaws.com)/32\"" > terraform.tfvars
cat terraform.tfvars

terraform init
terraform plan -out=compute.plan
terraform apply compute.plan
```

*Expected:* about 40 resources added, two to three minutes. Then:

```bash
terraform output
export GATEWAY=$(terraform output -raw gateway_url)
export CONSOLE=$(terraform output -raw console_url)
```

If you set `alarm_email`, **confirm the subscription from your inbox now**. An
unconfirmed subscription looks identical to a working one in `terraform apply`'s output
and delivers nothing.

---

## 7. Apply the migrations — the same runner, in the same image

Not `psql` from a laptop and not a bastion: RDS is in the private subnets, and BUILD_PLAN
§P9 says migrations run by the same runner as everywhere. This is that runner, as a
one-off ECS task on the gateway's own task definition — same image, same
`DATABASE_URL`, same code.

```bash
CLUSTER=$(terraform output -raw cluster_name)
TASKDEF=$(terraform output -raw gateway_task_definition)
NETWORK=$(terraform output -raw run_task_network_configuration)

TASK=$(aws ecs run-task --cluster "$CLUSTER" --task-definition "$TASKDEF" \
  --launch-type FARGATE --network-configuration "$NETWORK" \
  --overrides '{"containerOverrides":[{"name":"gateway","command":["uv","run","--no-sync","python","-m","headroom.db.migrate"]}]}' \
  --query 'tasks[0].taskArn' --output text)

aws ecs wait tasks-stopped --cluster "$CLUSTER" --tasks "$TASK"
aws logs tail "$(terraform output -raw gateway_log_group)" --since 10m | grep -i migration
```

*Expected:*

```
applied 7 migration(s): 0001_tenants_and_virtual_keys, 0002_usage_ledger,
0003_ledger_budget_columns, 0004_rate_limits, 0005_response_cache,
0006_ledger_failover, 0007_daily_rollups
```

`0005` is the interesting one: it runs `CREATE EXTENSION vector` against RDS for the first
time — locally that extension comes from the `pgvector/pgvector:pg16` image, and this is
where H-001's promise ("the Phase 9 RDS instance must be Postgres 16 with the `vector`
extension enabled from the RDS-supported list, which it is") is actually cashed. If it
fails, stop and paste the error: nothing downstream is worth running against a half-built
schema.

Then confirm the gateway is serving:

```bash
curl -sS "$GATEWAY/healthz"
```

*Expected:* `{"status":"ok"}`. If this times out, your address has changed since step 6 —
re-run `terraform apply` with a fresh `home_cidr`.

---

## 8. The gate's smoke

### 8a. A tenant and two keys — **free**

```bash
ADMIN=$(aws secretsmanager get-secret-value --secret-id headroom/admin-token \
  --query SecretString --output text)

TENANT=$(curl -sS -X POST "$GATEWAY/admin/tenants" \
  -H "Authorization: Bearer $ADMIN" -H 'content-type: application/json' \
  -d '{"name":"p9-smoke"}' | jq -r .id)

LIVE_KEY=$(curl -sS -X POST "$GATEWAY/admin/keys" \
  -H "Authorization: Bearer $ADMIN" -H 'content-type: application/json' \
  -d "{\"tenant_id\":\"$TENANT\",\"name\":\"live\"}" | jq -r .key)

MOCK_KEY=$(curl -sS -X POST "$GATEWAY/admin/keys" \
  -H "Authorization: Bearer $ADMIN" -H 'content-type: application/json' \
  -d "{\"tenant_id\":\"$TENANT\",\"name\":\"chaos\",\"allowed_models\":[\"mock-*\"]}" | jq -r .key)
```

*Expected:* a UUID and two `hk_…` strings. Each plaintext key exists exactly once, in the
response that created it (H-017).

### 8b. One live streamed request through the ALB — **~$0.001**

The gate's first clause, and the only step here that spends money.

```bash
curl -sS -N -D/tmp/p9-live-headers -X POST "$GATEWAY/v1/messages" \
  -H "Authorization: Bearer $LIVE_KEY" -H 'content-type: application/json' \
  -d '{"model":"claude-haiku-4-5","max_tokens":64,"stream":true,
       "messages":[{"role":"user","content":"Reply with exactly: headroom on aws"}]}' \
  | tee /tmp/p9-live-stream.txt | head -20

grep -Ei '^(HTTP|x-headroom)' /tmp/p9-live-headers
```

*Expected:* `HTTP/1.1 200 OK`, an `x-headroom-request-id`, **no `x-headroom-failover-*`
header at all**, and a stream that begins `event: message_start` and ends
`event: message_stop`. Then read the ledger row back — the number this whole project is
about:

```bash
REQ=$(grep -i x-headroom-request-id /tmp/p9-live-headers | tr -d '\r' | awk '{print $2}')
curl -sS "$GATEWAY/admin/usage/$REQ" -H "Authorization: Bearer $ADMIN" | jq \
  '{model, provider, input_tokens, output_tokens, usd_cost, cost_status,
    usd_per_mtok_in, usd_per_mtok_out, passthrough_overhead_ms, cache_disposition}'
```

*Expected:* `cost_status: "priced"`, a `usd_cost` string with twelve decimal places, the
rates it was billed at copied onto the row, `cache_disposition: "cache_disabled"`, and a
`passthrough_overhead_ms` in the tens of microseconds.

### 8c. The chaos suite's keyless subset, against the deployed stack — **$0.00**

`tests/test_failover_chaos.py` builds a gateway in-process, so it cannot be pointed at a
URL. `scripts/chaos_smoke.py` is the same properties asserted from the outside, with
every fault injected into the MockProvider over HTTP.

```bash
cd ../../..                                   # repo root
make chaos-smoke BASE_URL="$GATEWAY" KEY="$MOCK_KEY"
```

*Expected:* nine `ok` lines and `{"checks": 9, "failed": 0}` —

```
ok    no fault: 200, and no failover headers at all (…)
ok    fault-529@mock: 200 hops=1 from=mock (want 200 hops=1 from=mock)
ok    fault-timeout@mock: 200 hops=1 from=mock (want 200 hops=1 from=mock)
ok    fault-connect@mock: 200 hops=1 from=mock (want 200 hops=1 from=mock)
ok    fault-cut: the stream ends in a terminal error event
ok    fault-cut: the reason is upstream_stream_cut, not a generic api_error
ok    fault-cut: no message_stop — a cut answer never claims to have finished
ok    fault-cut: exactly one message_start (saw 1)
ok    fault-cut: HTTP 200 — the status line was spent before the fault
```

### 8d. Fire the rollup Lambda by hand, and see it in the console — **$0.00**

The gate's third clause. The nightly schedule is EventBridge's; this is the same function,
same event shape, on demand.

```bash
FUNCTION=$(terraform -chdir=deploy/aws/compute output -raw rollup_function_name)

aws lambda invoke --function-name "$FUNCTION" \
  --cli-binary-format raw-in-base64-out --payload '{}' \
  /tmp/p9-rollup.json >/dev/null
cat /tmp/p9-rollup.json | jq
```

*Expected:* the two days the scheduled run covers, today carrying the traffic from 8b and
8c:

```json
{
  "event": "daily_rollup",
  "days": [
    {"day": "…", "tenants": 0, "requests": 0, "usd_cost": "0"},
    {"day": "…", "tenants": 1, "requests": 6, "usd_cost": "0.00…"}
  ],
  "requests": 6,
  "duration_ms": …
}
```

If it returns a `Task timed out` or an `Unable to import module`, the package in step 5 is
the thing to look at. Read it back through the API the console reads:

```bash
curl -sS "$GATEWAY/admin/usage/rollups?limit=7" -H "Authorization: Bearer $ADMIN" | jq
```

Then open **`$CONSOLE`**, sign in with the admin token from step 4, and go to
**History**. The day's bar is the rollup you just fired; the *Last rollup* tile is the
`computed_at` stamp on it, and it is the only thing on that screen that can tell "no
traffic that day" from "nobody rolled that day up".

Fire it a second time and refresh: the numbers must not move. The rollup replaces a day
rather than accumulating into it, which is what makes the schedule safe to retry.

### 8e. The alarms — **$0.00**

```bash
aws cloudwatch describe-alarms --alarm-name-prefix headroom \
  --query 'MetricAlarms[].{Name:AlarmName,State:StateValue,Actions:AlarmActions[0]}' \
  --output table
```

*Expected:* four alarms — `headroom-5xx-rate`, `headroom-provider-down`,
`headroom-budget-refusals`, `headroom-ledger-rows-lost` — each with the SNS topic as its
action. `OK` or `INSUFFICIENT_DATA` are both fine on a stack that has served a handful of
requests.

To watch one fire, give a tenant a cap it has already exceeded:

```bash
curl -sS -X PUT "$GATEWAY/admin/budgets/$TENANT" -H "Authorization: Bearer $ADMIN" \
  -H 'content-type: application/json' -d '{"usd":"0.000001","window":"monthly"}'

curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$GATEWAY/v1/messages" \
  -H "Authorization: Bearer $MOCK_KEY" -H 'content-type: application/json' \
  -d '{"model":"mock-model-1","max_tokens":32,"messages":[{"role":"user","content":"hi"}]}'
```

*Expected:* `402`, and `headroom-budget-refusals` in `ALARM` within about five minutes —
with an email if you confirmed the subscription. Clear it afterwards:

```bash
curl -sS -X DELETE "$GATEWAY/admin/budgets/$TENANT" -H "Authorization: Bearer $ADMIN"
```

---

## 9. Capture the evidence — **before you destroy anything**

`docs/evidence/p9-aws/README.md` is the capture list. Evidence lives in the repo and
outside every blast radius (invariant 9) — a screenshot in an S3 bucket inside a
Terraform module is what Backline's teardown ate.

```bash
terraform -chdir=deploy/aws/compute output > docs/evidence/p9-aws/compute-outputs.txt
aws cloudwatch describe-alarms --alarm-name-prefix headroom > docs/evidence/p9-aws/alarms.json
cp /tmp/p9-rollup.json docs/evidence/p9-aws/rollup-invoke.json
```

Everything the console shows is gone the moment step 10 runs. Take the screenshots first.

---

## 10. Destroy the compute layer — **the daily cost drops to ~$0.53**

```bash
cd deploy/aws/compute
terraform destroy
```

*Expected:* about 40 resources destroyed, three to four minutes (the load balancer and its
ENIs are the slow part). Then the property the whole two-root split exists for:

```bash
terraform -chdir=../data plan
```

*Expected:* **`No changes. Your infrastructure matches the configuration.`** The data layer
is untouched: RDS, both DynamoDB tables, both ECR repositories, and the three secrets are
exactly as they were. Phase 10 starts from here.

---

## 11. The empty checks — per service, not the tag scan

§P9's words: *"per-service empty checks (not the tag scan — tombstones lie)"*. A resource
in a deleting state still carries its tags and still answers a tag query, so a tag scan
that comes back clean has told you nothing about what is still billing.

```bash
REGION=us-east-1

# Nothing running, and no service left to run it
aws ecs list-clusters --region $REGION
aws ecs list-services --cluster headroom --region $REGION 2>&1 | head -2

# No load balancers, no target groups
aws elbv2 describe-load-balancers --region $REGION --query 'LoadBalancers[].LoadBalancerName'
aws elbv2 describe-target-groups --region $REGION --query 'TargetGroups[].TargetGroupName'

# No function, no schedule
aws lambda list-functions --region $REGION --query 'Functions[?starts_with(FunctionName, `headroom`)].FunctionName'
aws events list-rules --region $REGION --query 'Rules[?starts_with(Name, `headroom`)].Name'

# No log groups — the classic leftover, because whoever creates one implicitly owns it
aws logs describe-log-groups --region $REGION \
  --query 'logGroups[?contains(logGroupName, `headroom`)].logGroupName'

# No alarms, no topic
aws cloudwatch describe-alarms --alarm-name-prefix headroom --region $REGION \
  --query 'MetricAlarms[].AlarmName'
aws sns list-topics --region $REGION --query 'Topics[?contains(TopicArn, `headroom`)]'

# No interface endpoint (the $0.48/day one)
aws ec2 describe-vpc-endpoints --region $REGION \
  --query 'VpcEndpoints[?VpcEndpointType==`Interface`].[ServiceName,State]' --output table

# And what SHOULD still be there
aws rds describe-db-instances --region $REGION --query 'DBInstances[].DBInstanceIdentifier'
aws dynamodb list-tables --region $REGION
aws ecr describe-repositories --region $REGION --query 'repositories[].repositoryName'
```

*Expected:* empty lists for everything above the last block, and `headroom-db`,
`headroom_budgets`/`headroom_buckets`, `headroom/gateway`/`headroom/ui` for the last one.

Two things a destroy does not remove and that are worth knowing about:

- **RDS's own generated-password secret** (`rds!db-…`) goes to a recovery window rather
  than away. It is free, and `aws secretsmanager list-secrets --include-planned-deletion`
  shows it.
- **A Fargate task's ENI** can linger for a minute after the service is gone. It is free
  and it disappears on its own; if `terraform destroy` of the *VPC* ever hangs on
  `DependencyViolation`, this is why — wait a minute and retry.

---

## 12. End of Phase 10 — destroy the data layer

Not part of Phase 9. Written here so it is in one place when the EKS window closes.

```bash
cd deploy/aws/data
terraform destroy
```

*Expected:* RDS takes several minutes. `force_delete` on the ECR repositories is what lets
this succeed with images still in them, and `recovery_window_in_days = 0` is what lets the
three secrets be re-created under the same names afterwards.

```bash
aws rds describe-db-instances --region $REGION --query 'DBInstances[].DBInstanceIdentifier'
aws rds describe-db-snapshots --region $REGION --query 'DBSnapshots[].DBSnapshotIdentifier'
aws dynamodb list-tables --region $REGION
aws ecr describe-repositories --region $REGION --query 'repositories[].repositoryName'
aws secretsmanager list-secrets --region $REGION --query 'SecretList[].Name'
aws ec2 describe-vpcs --region $REGION --query 'Vpcs[?!IsDefault].VpcId'
```

*Expected:* every one of them empty. The snapshot check earns its place: `backup_retention_period = 0`
and `skip_final_snapshot = true` are the two flags that make it so, and a snapshot nobody
knows about bills for storage long after the instance it came from is gone.

---

## Reference — what runs where

| Thing | Where | Why |
|---|---|---|
| Gateway, console | Fargate, **public** subnets, public IP | a route to Anthropic and ECR with no NAT gateway; inbound only from the ALB's security group |
| RDS | **private** subnets, no public address | the door in is an ECS task on the same VPC, which is also the door migrations come through |
| Rollup Lambda | **private** subnets | it needs RDS; it reaches Secrets Manager through an interface endpoint |
| DynamoDB | gateway endpoint | free, and the conditional writes never leave the VPC |
| Console → gateway | Cloud Map (`gateway.headroom.local`) | so the ALB's security group can stay at exactly one source address |

**The vLLM chain is unreachable from AWS, by design.** `config/routing.yaml` sends every
non-`mock-` OpenAI-dialect model to `vllm_a` with `vllm_b` as its fallback, and both are
the operator's own 4090s at home. On AWS those requests fail over once and then fail,
honestly, with `upstream_unavailable` — and they will trip the `provider-down` alarm if
you send enough of them. The smoke above uses `claude-*` and `mock-*` for that reason.
Reaching home from the cluster is Phase 10's problem, and tailscale is its answer.
