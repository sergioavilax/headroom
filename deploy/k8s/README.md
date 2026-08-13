# Headroom on EKS — the operator's runbook (the three-day window)

Every command in dependency order, with what it costs stated **before** it runs.

**Claude Code writes this file and never executes it.** BUILD_PLAN §0.2 invariant 2: the
human runs every `eksctl`, every `helm install`, every `helm uninstall`, every `kubectl`
mutation, and every AWS mutation. Two terminals — the agent in one, your hands in the
other. Errors get pasted back, never guessed around.

---

## 0. What this is

Phase 9 left a **data layer** standing: a VPC, RDS Postgres 16 with pgvector, two DynamoDB
tables, two ECR repositories holding pushed images, and three filled secrets. It has been
accruing about $0.53/day since. This phase puts a Kubernetes cluster in front of it for
three days, proves five things, and then takes both down.

| Layer | Made by | Lives |
|---|---|---|
| `deploy/aws/data` | Terraform, Phase 9 | since 2026-08-10, **destroyed at the end of this phase** |
| the EKS cluster | `eksctl`, this phase | three days |
| the gateway and console | `helm`, this phase | inside the cluster's life |

**The data layer is not rebuilt as pods, and that is the argument.** §P10: *"using managed
services from k8s IS the realistic architecture"*. A Postgres StatefulSet with an EBS
volume would be a demo of Kubernetes rather than a deployment of Headroom, and would throw
away the RDS instance that already holds seven applied migrations and a Phase 9 ledger.

### What has to be true before you start

- `deploy/aws/data/terraform.tfstate` is on this machine and the data layer still stands.
  `docs/evidence/p9-aws/16-data-plan-after-destroy.txt` is the last thing that said so.
- The two ECR repositories still hold the images Phase 9 pushed
  (`docs/evidence/p9-aws/03-images-pushed.txt`). Re-pushing 810 MiB from a home connection
  is the slowest step in this project and this phase is arranged not to need it.
- Both vLLM instances at home are the ones `docs/vllm.md` documents — assumption **A6**,
  re-checked in §3.

### Prerequisites

```bash
terraform version        # >= 1.9
eksctl version           # >= 0.190
kubectl version --client
helm version --short     # >= 3.14
aws sts get-caller-identity

# Not optional, and not implied by anything else in this file. Terraform reads its region
# from `var.region`; `eksctl` reads it from the cluster config; `kubectl` reads it from
# nothing at all; and the `aws` CLI reads it from here.
export AWS_DEFAULT_REGION=us-east-1
echo "$AWS_DEFAULT_REGION"
```

**Pin the region in every terminal, including the second one.** This cost the Phase 9 run
an evening (**H-083**). A CLI profile left pointing at another project's region sends
`terraform apply` to `us-east-1` — it has `var.region` — and every `aws secretsmanager
get-secret-value` in §5 somewhere else, where the failure reads:

```
An error occurred (ResourceNotFoundException) when calling the GetSecretValue operation:
Secrets Manager can't find the specified secret.
```

Which is true, and is the most misleading sentence in this document: the secret exists and
is two thousand miles away. `aws secretsmanager list-secrets` returning an empty list is
the tell. This runbook is explicitly two terminals and three days, so a new tab, a reboot,
or tomorrow morning starts without it.

### What it costs

List price, `us-east-1`, one cluster, two nodes, one load balancer:

| Line | Rate | Per day |
|---|---|---:|
| EKS control plane | $0.10/hr | **$2.40** |
| 2 x `t3.medium` on demand | $0.0416/hr each | **$2.00** |
| Network Load Balancer (the gateway's front door) | $0.0225/hr + NLCU | **$0.54** |
| 2 x 20 GiB gp3 root volumes | $0.08/GB-month | $0.11 |
| **The data layer**, still standing from Phase 9 | RDS + DynamoDB + ECR + secrets | **$0.53** |
| **Total, cluster up** | | **≈ $5.58 per day** |
| **Total, after §14** (data layer alone) | | **≈ $0.53 per day** |
| **Total, after §16** | | **$0.00** |

**Projected for a three-day window: $17–19**, against §0.4's **A7** estimate of **$20–25**
and §0.6's P10 line. The number to watch is the control plane: at $2.40/day it is the
largest single line and it is charged whether or not a pod is running, which is what makes
"the window is three days and then it is gone" a cost decision rather than a preference.

Two lines are worth arguing with before you spend them:

- **`t3.medium` rather than `t3.small`.** 2 vCPU and 4 GiB. A `t3.small` is half the price
  and cannot hold two gateway pods plus the surge pod of a rolling upgrade — and the
  failure mode is a pod stuck in `Pending`, which looks like nothing at all.
  `tests/test_deploy_k8s.py::test_every_pod_this_chart_schedules_fits_on_the_node_group_it_targets`
  does that arithmetic so this line is a decision rather than a hope.
- **One load balancer, not two.** Phase 9 put the console behind the same ALB as the
  gateway because ECS has no other way to reach a task. Kubernetes has `kubectl
  port-forward`: free, and guarded by IAM and RBAC rather than by an IP allow-list in
  front of a cleartext listener. So the console is a ClusterIP service and §9 reaches it
  through kubectl.

### One stated limitation, before you rely on it

**The gateway's listener is HTTP.** There is no domain, so there is no ACM certificate,
and the security control is the `/32` allow-list in `loadBalancerSourceRanges` — the same
control §P9 asked for by name, on the same terms. The cost is real and it is the same
cost: the root admin token crosses your own connection to the load balancer in the clear.
What production adds is a hostname, a certificate, and a listener that redirects.

### The shape of the three days

| | What happens | What it leaves behind |
|---|---|---|
| **Day 1** | warm-up, the data layer's one change, create the cluster, install the chart, smoke it | the cluster serving, `01`–`08` |
| **Day 2** | the rolling upgrade under load, the dashboard from the cluster, a billing check | `09`–`13` |
| **Day 3** | the two-vLLM failover from the cluster, then teardown and the empty checks | `14`–`20` |

The capture list is `docs/evidence/p10-eks/README.md` and the numbers above are its rows.
**Capture before you destroy**: everything in a cluster is gone the moment §14 runs.

---

# Day 1 — warm up, create, deploy, smoke

## 1. The two things Phase 9 left open — **$0.00, and not a gate**

Both are carried items from the Phase 9 close-out, and **H-080 as amended** puts the first
one here by name: *"the retry belongs at the start of P10's first session, before the
cluster exists."*

Phase 9 activated exactly one of the four cost-allocation tag keys. `Layer`, `Phase`, and
`ManagedBy` each answered `ValidationException: tag key missing` for the whole session,
because Billing's discovery of a tag key it has never seen is asynchronous and slow —
hours, possibly next-day, and not a function of anything an operator can do faster. The
resources have carried all four tags since their first second, which is the half that
cannot be retrofitted; activation is what makes Cost Explorer *group* by them.

```bash
aws ce update-cost-allocation-tags-status --cost-allocation-tags-status \
  TagKey=Project,Status=Active TagKey=Layer,Status=Active \
  TagKey=Phase,Status=Active TagKey=ManagedBy,Status=Active

aws ce list-cost-allocation-tags --status Active \
  --query 'CostAllocationTags[].{Key:TagKey,Status:Status}' --output table
```

*Expected, by now:* four rows, all `Active`. If any still answers `ValidationException`,
**do not hold the runbook for it** — re-run these two commands at the start of Day 2 and
Day 3. This matters more than it did in Phase 9: **A7's estimate-versus-actual table in
§17 needs `Layer` active for the whole window**, and a key activated on Day 3 groups
nothing that happened on Day 1.

> **⚠️ This instruction is what actually failed on the run — see §17 and H-102.** *"Do not
> hold the runbook for it"* is wrong when the window is short. `Layer`, `Phase` and
> `ManagedBy` went `Active` at 16:54 UTC, most of the way through a fourteen-hour window, and
> because cost allocation tags are **never backfilled**, 72.4% of the bill came back with no
> `Layer` value even though every resource carried the tag. **If you want an attributable
> bill: hold here until all four read `Active`.** The only thing standing at this point is an
> empty ECR repository, so waiting costs nothing — which is the whole reason §1 seeds the keys
> from ECR in the first place. Hold the runbook, or accept that §17 will produce two totals
> and a gap you cannot explain.

When they are all Active, take the screenshot Phase 9 could not:

- **Billing → Cost allocation tags**, showing the four keys Active and dated →
  `docs/evidence/p9-aws/02-cost-allocation-tags.png`

`docs/evidence/p9-aws/18-billing.png` — Cost Explorer filtered to `Project=headroom`,
split by `Layer` — was to land in §17 with this phase's own billing capture, because Cost
Explorer needs up to 24 hours after activation on top of everything else. **It was not
captured:** by the time `Layer` was active there was no Phase 9 split left to show, which is
the warning above, measured.

## 2. The data layer's one change — **$0.00, in place**

Two Kubernetes discovery tags on the two public subnets, and one new output. Nothing is
replaced; RDS never notices.

```bash
terraform -chdir=deploy/aws/data plan -out=p10.plan
```

*Expected:* `Plan: 0 to add, 2 to change, 0 to destroy.` — the two `aws_subnet.public`
resources gaining `kubernetes.io/cluster/headroom = shared` and `kubernetes.io/role/elb = 1`.
**If it says anything is being destroyed or replaced, stop and paste it.** The data layer
holds the only copy of the Phase 9 ledger.

```bash
terraform -chdir=deploy/aws/data apply p10.plan
terraform -chdir=deploy/aws/data output public_subnet_azs
```

*Expected:* `["us-east-1a", "us-east-1b"]`. That output is new in this phase and the
config generator needs it.

Then regenerate the two config files and check the committed one has not moved:

```bash
make k8s-config \
  HOME_CIDR="$(curl -s https://checkip.amazonaws.com)/32" \
  VLLM_TARGET_IP="$(tailscale ip -4 2>/dev/null || echo '')"

git diff --stat deploy/k8s/eksctl/cluster.yaml
```

*Expected:* **no diff**. `cluster.yaml` is committed precisely so this check exists: an
empty diff means the VPC, subnets, security group and table ARNs it names are still the
ones the data layer has. A diff means the data layer moved, and the diff says how — read
it before creating a cluster against it.

`deploy/k8s/values.aws.yaml` is written too and is **gitignored**: it carries your home
address. `cat` it once and check the CIDR is a `/32` and is yours.

> `tailscale ip -4` prints *this* machine's tailnet address, which is the machine the two
> vLLM containers publish their ports on. **If tailscale is installed on the Windows side
> of WSL rather than in it, that command prints nothing**, `vllm.enabled` comes out
> `false`, and the cluster is built with no path home — which is a perfectly good gateway
> and not what Day 3 needs. Take the address out of `tailscale status` instead and re-run
> `make k8s-config` with it once §3 has confirmed it. The chart refuses to render a
> tailscale proxy with an empty `targetIP`, so the failure is loud either way.

## 3. Pre-flight the path home — **$0.00**

The failover demo on Day 3 needs a pod in `us-east-1` to open a TCP connection to a
container on your desk. Three things have to be true, and finding out on Day 3 is the
expensive way.

```bash
# A6: both instances serving, with the known-good parser flags (docs/vllm.md).
for p in 8010 8011; do curl -sS "http://localhost:$p/v1/models" | head -c 120; echo; done

# The same two ports, on this machine's *tailnet* address rather than on localhost.
TS_IP=$(tailscale ip -4)
for p in 8010 8011; do curl -sS --max-time 5 "http://$TS_IP:$p/v1/models" | head -c 60; echo; done
```

*Expected:* `cyankiwi/Qwen3.6-27B-AWQ-INT4` from all four. **The second pair is the one
that usually fails**, and the cause is almost always the host firewall rather than
tailscale: Docker publishes `-p 8010:8000` on `0.0.0.0`, so the port is bound, but Windows
Defender Firewall blocks inbound connections on the Tailscale interface by default. Allow
inbound TCP 8010 and 8011 on the Tailscale adapter and re-run.

Then mint the key the egress pod joins with, in the tailscale admin console
(**Settings → Keys → Generate auth key**):

- **Ephemeral** — the device disappears from your tailnet when the pod does, which is what
  stops this phase leaving something behind on somebody's network.
- **Pre-approved** — a pod cannot click "approve" in a browser.
- **Tagged** `tag:headroom-k8s`, and grant that tag access to this machine on 8010–8011 in
  your ACL. A key with no tag inherits *your* user's access, which is more than a proxy
  needs.

Keep it for §5. It is a credential: leading space, never in a file, never in this repo.

## 4. Create the cluster — **$4.40/day starts here**

The control plane and the two nodes begin billing the moment this starts, and the control
plane bills whether or not anything is scheduled.

```bash
eksctl create cluster -f deploy/k8s/eksctl/cluster.yaml
```

*Expected:* fifteen to twenty minutes, ending in
`EKS cluster "headroom" in "us-east-1" region is ready`. eksctl builds two CloudFormation
stacks (the cluster and the node group), an OIDC provider, and the IAM role behind the
`headroom-gateway` service account, and writes your kubeconfig.

```bash
kubectl config current-context
kubectl get nodes -o wide
kubectl get sa -n headroom headroom-gateway -o jsonpath='{.metadata.annotations}'
kubectl version -o json | jq '{client: .clientVersion.gitVersion, server: .serverVersion.gitVersion}'
```

*Expected:* two nodes `Ready` with **public** IPs (there is no NAT gateway — H-075, and
the nodes need a route to ECR); the service account carrying an
`eks.amazonaws.com/role-arn` annotation; and a server version, which goes in the evidence
because this config deliberately pins no Kubernetes version.

→ `01-cluster-created.txt`, `02-nodes.txt`

**If the node group fails to reach `Ready`**, the two usual causes on an existing VPC are
a subnet with no route to the internet gateway (this VPC's public route table has one) and
a security group that blocks the kubelet. Paste the CloudFormation event; do not delete
and retry blind — a half-created cluster still bills.

## 5. The secrets, by hand — **$0.00**

Four values, from Secrets Manager and from §3. **Note the leading space on each `kubectl
create secret`**: it keeps them out of shell history where `HISTCONTROL=ignorespace` is
set, exactly as the Phase 9 runbook's §4 does.

```bash
DB=$(aws secretsmanager get-secret-value --secret-id headroom/database-url \
  --query SecretString --output text)
ADMIN=$(aws secretsmanager get-secret-value --secret-id headroom/admin-token \
  --query SecretString --output text)
ANTHROPIC=$(aws secretsmanager get-secret-value --secret-id headroom/anthropic-api-key \
  --query SecretString --output text)

 kubectl -n headroom create secret generic headroom-secrets \
   --from-literal=DATABASE_URL="$DB" \
   --from-literal=HEADROOM_ADMIN_TOKEN="$ADMIN" \
   --from-literal=ANTHROPIC_API_KEY="$ANTHROPIC"

 kubectl -n headroom create secret generic headroom-tailscale \
   --from-literal=TS_AUTHKEY="tskey-auth-..."

unset DB ADMIN ANTHROPIC
```

*Expected:* `secret/headroom-secrets created` and `secret/headroom-tailscale created`.
The `headroom` namespace already exists — eksctl made it in §4 for the service account.

```bash
kubectl -n headroom get secret headroom-secrets -o jsonpath='{.data}' | jq 'keys'
```

*Expected:* `["ANTHROPIC_API_KEY","DATABASE_URL","HEADROOM_ADMIN_TOKEN"]` — the key names
and not the values. Those three names are the environment variables the gateway reads,
unchanged from `docker-compose.yml` and from the ECS task definition, which is the whole
of the parity claim: the three environments differ in where a value comes from and in
nothing else.

**What a Kubernetes Secret is and is not, said plainly.** It is base64 in etcd, not
encryption. On EKS the etcd volume is encrypted by AWS and the value never leaves the
control plane, but anyone with `get secrets` in this namespace can read it back — which is
the same trust boundary the ECS execution role had, and the reason there is exactly one
human with credentials to this cluster. What production adds is envelope encryption with a
KMS key, a Secrets Store CSI driver or External Secrets Operator so the value is never
typed at all, and RBAC with more than one subject in it. None of that is claimed here.

**Why not External Secrets Operator now?** It would be a second Helm release, a set of
CRDs, an IRSA role, and a `ClusterSecretStore` — to replace one command that runs once in
a three-day window. It earns its complexity when secrets rotate, when more than one person
deploys, or when "who typed that" is a question somebody has to answer; none of those is
true here, and installing it to look thorough would be the kind of decoration §P9 refused
when it declined to make the rollup a cron task. **H-086.**

## 6. Install the chart — **$0.54/day more, for the load balancer**

Render it first. `helm template` runs the same guards `helm install` does, and finds a
missing source range or a missing tailnet address without creating anything:

```bash
helm template headroom deploy/k8s/headroom -n headroom -f deploy/k8s/values.aws.yaml \
  | tee /tmp/p10-rendered.yaml | grep -E '^kind:' | sort | uniq -c
```

*Expected:* a Deployment, Service, Job, PodDisruptionBudget set — and **no** `kind: Secret`,
because the chart declares none and has no field to put a value in.

```bash
helm upgrade --install headroom deploy/k8s/headroom \
  -n headroom -f deploy/k8s/values.aws.yaml \
  --wait --timeout 15m
```

*Expected:* the migration hook runs first and to completion, then the rollout. The first
pull is the slow part — 810 MiB of gateway image onto a node that has never seen it, which
is why the chart's startup probe allows five minutes.

```bash
kubectl -n headroom logs job/headroom-migrate
```

*Expected:* `up to date` — Phase 9 already applied all seven migrations to this RDS
instance, so this is the confirmation that the chart is pointed at the right database and
not a change to it. On a fresh database it would read `applied 7 migration(s): …`.

```bash
kubectl -n headroom get pods -o wide
kubectl -n headroom get svc
```

*Expected:* two `headroom-gateway` pods on **different nodes** (the anti-affinity is a
preference and it is doing real work), one `headroom-ui`, one `headroom-vllm`, and a
`headroom-gateway` service whose `EXTERNAL-IP` is a `…elb.amazonaws.com` hostname within a
minute or two.

```bash
export GATEWAY="http://$(kubectl -n headroom get svc headroom-gateway \
  -o jsonpath='{.status.loadBalancer.ingress[0].hostname}'):8080"
echo "$GATEWAY"
curl -sS "$GATEWAY/healthz"
```

*Expected:* `{"status":"ok"}`.

→ `03-helm-install.txt`, `04-pods-svc.txt`, `05-migrate-job.txt`

### If `EXTERNAL-IP` stays `<pending>`

This is the one step in this phase that depends on an AWS behaviour nothing in this repo
can verify keylessly — the same position `CREATE EXTENSION vector` held in Phase 9. Read
the events before changing anything:

```bash
kubectl -n headroom describe svc headroom-gateway | sed -n '/Events/,$p'
```

Three causes, in the order they are likely:

1. **The subnets are not tagged.** §2 is what fixes it; check with
   `aws ec2 describe-subnets --subnet-ids <the two> --query 'Subnets[].Tags'`. The event
   says `could not find any suitable subnets for creating the ELB`.
2. **The cluster is new enough that `Service` type LoadBalancer needs the AWS Load
   Balancer Controller.** The event says nothing was reconciled at all. Install it —
   `eksctl create iamserviceaccount` for its policy, then its own Helm chart — and change
   the two annotations in `values.aws.yaml` to
   `service.beta.kubernetes.io/aws-load-balancer-type: external` plus
   `-nlb-target-type: ip`. That is a values edit by design: no template in this chart
   mentions AWS.
3. **The quota.** An account at its load-balancer limit fails here and nowhere else.

The fallback that needs no controller at all, if the window is short: set
`gateway.service.type=NodePort`, allow your `/32` to the node port on the cluster's shared
node security group, and use a node's public IP. It is uglier and it demonstrates the same
five things.

## 7. The smoke — **~$0.001, and it is the only paid step in this phase**

### 7a. A tenant and two keys — free

```bash
ADMIN=$(aws secretsmanager get-secret-value --secret-id headroom/admin-token \
  --query SecretString --output text)

TENANT=$(curl -sS -X POST "$GATEWAY/admin/tenants" \
  -H "Authorization: Bearer $ADMIN" -H 'content-type: application/json' \
  -d '{"name":"p10-smoke"}' | jq -r .id)

LIVE_KEY=$(curl -sS -X POST "$GATEWAY/admin/keys" \
  -H "Authorization: Bearer $ADMIN" -H 'content-type: application/json' \
  -d "{\"tenant_id\":\"$TENANT\",\"name\":\"live\"}" | jq -r .key)

MOCK_KEY=$(curl -sS -X POST "$GATEWAY/admin/keys" \
  -H "Authorization: Bearer $ADMIN" -H 'content-type: application/json' \
  -d "{\"tenant_id\":\"$TENANT\",\"name\":\"loops\"}" | jq -r .key)
```

*Expected:* a UUID and two `hk_…` strings. Keep both keys for Days 2 and 3 — each
plaintext exists exactly once, in the response that created it (H-017), so a lost key is a
new key.

### 7b. One live streamed request through the load balancer — ~$0.001

```bash
curl -sS -N -D/tmp/p10-live-headers -X POST "$GATEWAY/v1/messages" \
  -H "Authorization: Bearer $LIVE_KEY" -H 'content-type: application/json' \
  -d '{"model":"claude-haiku-4-5","max_tokens":64,"stream":true,
       "messages":[{"role":"user","content":"Reply with exactly: headroom on eks"}]}' \
  | tee /tmp/p10-live-stream.txt | head -20

grep -Ei '^(HTTP|x-headroom)' /tmp/p10-live-headers

REQ=$(grep -i x-headroom-request-id /tmp/p10-live-headers | tr -d '\r' | awk '{print $2}')
curl -sS "$GATEWAY/admin/usage/$REQ" -H "Authorization: Bearer $ADMIN" | jq \
  '{model, provider, input_tokens, output_tokens, usd_cost, cost_status,
    usd_per_mtok_in, usd_per_mtok_out, passthrough_overhead_ms, cache_disposition}'
```

*Expected:* `HTTP/1.1 200`, an `x-headroom-request-id`, **no `x-headroom-failover-*`
header of any kind**, a stream that begins `event: message_start` and ends
`event: message_stop`, and a row with `cost_status: "priced"`, the two rates it was billed
at copied onto it, and a `passthrough_overhead_ms` in the tens of microseconds. The same
request, through a third kind of load balancer, writing the same row.

→ `06-live-request-headers.txt`, `07-live-ledger-row.json`

### 7c. The chaos subset, against the cluster — $0.00

The identical command Phase 9 ran against the ALB, and Phase 6 wrote for a compose stack.

```bash
make chaos-smoke BASE_URL="$GATEWAY" KEY="$MOCK_KEY"
```

*Expected:* nine `ok` lines and `{"checks": 9, "failed": 0}`. The one to look at is
`exactly one message_start` — H-048's splice test asserted from outside a load balancer,
a kube-proxy, and two pods.

→ `08-chaos-smoke.txt`

---

# Day 2 — the rolling upgrade, and the dashboard

Re-export `AWS_DEFAULT_REGION`, `GATEWAY`, `ADMIN`, `LIVE_KEY` and `MOCK_KEY` in whatever
terminal you are in today. Re-run §1's two commands if the tag keys were not all Active
yesterday.

## 8. A rolling `helm upgrade` with zero dropped requests — **$0.00**

§P10's second proof. Three terminals: the loop, the upgrade, and a watch.

First give the upgrade something to change. `docker tag` and push under a second tag — the
layers are already in ECR, so this is a manifest and takes seconds:

```bash
GATEWAY_REPO=$(terraform -chdir=deploy/aws/data output -raw gateway_repository_url)
REGION=$(terraform -chdir=deploy/aws/data output -raw region)
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "$ACCOUNT.dkr.ecr.$REGION.amazonaws.com"

docker pull "$GATEWAY_REPO:latest"
docker tag  "$GATEWAY_REPO:latest" "$GATEWAY_REPO:p10"
docker push "$GATEWAY_REPO:p10"
```

> The two tags name the same digest, so the nodes already hold every layer and the pull is
> instant. That does not weaken the measurement: what is being measured is whether a *pod
> replacement* drops a request, and `maxUnavailable: 0` means a slow pull would make the
> rollout longer and the answer easier, not harder. The honest version of the claim is in
> the evidence README.

**Terminal A — the loop.** Ten minutes, four requests in flight, streamed, which is the
harder test: an in-flight stream is what a terminating pod is most able to break. `--timeout`
is explicit because the default is tuned to the mock provider and a timeout is an assumption
about the model behind it (H-092); against `mock-` models 15 s is right, and saying so here
is what stops §11's mistake being made twice.

```bash
uv run python scripts/load_loop.py \
  --base-url "$GATEWAY" --key "$MOCK_KEY" \
  --duration-s 600 --concurrency 4 --interval-ms 200 --stream --timeout 15 \
  --label "rolling-upgrade" --out /tmp/p10-load-loop.json
```

**Terminal B — wait about a minute, then upgrade.**

```bash
helm upgrade headroom deploy/k8s/headroom \
  -n headroom -f deploy/k8s/values.aws.yaml \
  --set image.tag=p10 --wait --timeout 10m
```

**Terminal C — watch it happen.**

```bash
kubectl -n headroom rollout status deploy/headroom-gateway --watch
kubectl -n headroom get pods -w
```

*Expected in C:* a third pod appears and becomes Ready before an old one is touched
(`maxSurge: 1`, `maxUnavailable: 0`), then the same again. At no point are fewer than two
pods Ready.

> **The terminating pods exit `Error`, and that is correct.** It looks exactly like a
> container that never shut down gracefully, and it is not: uvicorn re-raises the signal it
> shut down for so that a parent sees "terminated by SIGTERM" the way Unix expects, the
> process therefore exits 143, and Kubernetes renders any non-zero exit as `Error`. The
> shutdown itself is clean and the H-027 ledger drain runs — `kubectl -n headroom logs
> <old-pod> --previous | tail -4` shows `Application shutdown complete` if you want to see
> it. H-091 has the full autopsy; it cost an afternoon of suspecting the entrypoint.

*Expected in A*, when the ten minutes are up:

```json
{
  "label": "rolling-upgrade",
  "requests": …, "ok": …, "shed": 0, "dropped": 0,
  "max_gap_ms": …,
  "incidents": []
}
```

**`dropped: 0` is the claim and `max_gap_ms` is the one that catches what a count cannot.**
A rollout that dropped nothing but was unreachable for nine seconds has an error count of
zero and is still an outage; the gap is the longest stretch of the run with no successful
response, and at four workers 200 ms apart a healthy run keeps it in the low hundreds of
milliseconds. Exit code 0 means `dropped` was zero.

If anything did drop, the summary carries the first two hundred incidents with a timestamp
relative to the start of the run, a status, and the transport error where there was no
status at all — which is enough to line each one up against `kubectl get events`. **Report
it.** A phase log that records a rollout dropping four requests out of twelve thousand is
worth more than one that records a zero nobody could have falsified.

```bash
kubectl -n headroom get events --sort-by=.lastTimestamp | tail -30
helm history headroom -n headroom
```

→ `09-load-loop.json`, `10-rollout.txt`, `11-helm-history.txt`

### What the first two runs found, and what changed because of it

They did not read zero, and the paragraph above is why they are still here:

```
run 1  preStopSleepSeconds=5    8331 requests   1 dropped   t=87s          max_gap 402 ms
run 2  preStopSleepSeconds=15   8326 requests   2 dropped   t=77s, t=83s   max_gap 258 ms
```

One drop per replaced pod, every one of them
`RemoteProtocolError: Server disconnected without sending a response.`, and **tripling the
sleep changed nothing** — which is the whole diagnosis. The sleep covers the race on the
*new connection* side while kube-proxy catches up; it has no reach at all over a connection
that already exists, because conntrack pins an established flow to the pod it was given to
and Endpoints has no say. A client holding keep-alive connections spends the entire sleep
talking to the pod that is about to stop, and loses whatever it had written when uvicorn
closes them.

So `preStop` now touches a sentinel file before it sleeps, and a pod that has seen that
sentinel answers every response with `Connection: close`. Clients retire those connections
themselves, during the sleep, one response at a time — and open the next one against a pod
Endpoints has already moved them to. H-091 has the argument and the alternatives; the two
values are `gateway.lifecycle.drainFilePath` and `gateway.lifecycle.preStopSleepSeconds`.

**It is reproducible without a cluster**, which is what stopped this being guesswork. Two
gateway containers, a sixty-line kube-proxy that pins established connections, the same load
loop across the switch — and one round trip of emulated latency, without which the race
cannot fire at all on loopback because `httpcore` notices the server's FIN before it reuses
the socket:

```bash
scripts/rollout_repro.sh baseline   # 1-2 dropped, RemoteProtocolError, at the SIGTERM instant
scripts/rollout_repro.sh drain      # 0 dropped
```

### Run 3 — the same measurement, against the fix

Build and push a new image (the drain lives in the gateway, so `p10` is not it), then run
§8 again unchanged:

```bash
docker build -t "$GATEWAY_REPO:p10-drain" --build-arg WITH_EMBED=1 .
docker push "$GATEWAY_REPO:p10-drain"

helm upgrade headroom deploy/k8s/headroom \
  -n headroom -f deploy/k8s/values.aws.yaml \
  --set image.tag=p10-drain --wait --timeout 10m
```

The upgrade that rolls the fix out is itself measured by the *old* build, so run 3 is the
upgrade *after* it — pods on both sides of that rollout have to be draining pods for the
claim to mean anything. Roll `p10-drain` on, let it settle, then start the loop in Terminal A
and upgrade back to `p10` in Terminal B: same chart, same values, two draining pods being
replaced by two draining pods.

*Expected:* `dropped: 0`, `incidents: []`, `max_gap_ms` in the low hundreds, exit code 0.
**If it is not zero, that is the result** — the residual H-091 names is a connection idle for
the whole drain window and first reused in the milliseconds after SIGTERM, and a run that
catches one has found the thing the decision record says it cannot rule out.

## 9. The dashboard, served from the cluster — **$0.00**

```bash
kubectl -n headroom get pods -l app.kubernetes.io/component=ui -o wide
kubectl -n headroom port-forward svc/headroom-ui 3001:3001
```

Open `http://localhost:3001`, sign in with the admin token from §5, and walk the views.
The console is a client of `/admin/*` and nothing else (H-054); it is reaching the gateway
by its ClusterIP service, which is this cluster's version of Phase 9's Cloud Map name.

Capture **Overview** and **Requests** at least. The Requests view should show the live
request from §7b beside the mock traffic from §8 — one tenant, two very different rows.

→ `12-console-overview.png`, `13-console-requests.png`

> The address bar says `localhost` because the port-forward is the door. `04-pods-svc.txt`
> says which node the pod is on; between them the claim "served from the cluster" is
> checkable. A second load balancer would have put a hostname in the screenshot and $0.54 a
> day on the bill for it.

## 10. The day-2 billing check — **$0.00**

Not the estimate-versus-actual table — that is §17 — but the check that catches drift
while there is still time to act on it (risk register item 4).

```bash
aws ce get-cost-and-usage \
  --time-period Start=$(date -u -d '2 days ago' +%F),End=$(date -u +%F) \
  --granularity DAILY --metrics UnblendedCost \
  --group-by Type=DIMENSION,Key=SERVICE \
  --query 'ResultsByTime[].{Day:TimePeriod.Start,Groups:Groups[?Metrics.UnblendedCost.Amount!=`0`].{S:Keys[0],A:Metrics.UnblendedCost.Amount}}'
```

*Expected:* a day of cluster uptime landing near **$5.58**, dominated by
`Amazon Elastic Container Service for Kubernetes` and `Amazon Elastic Compute Cloud`. If it
is materially above, find out why today: the hard three-day limit exists because a cluster
left up is the single largest cost risk in this project.

---

# Day 3 — the failover demo, then teardown

## 11. The two-vLLM failover, pointed at the cluster gateway — **$0.00**

The demo Phase 6 built and Phase 7 filmed, now with the gateway in `us-east-1` and the
GPUs on a desk. `config/routing.yaml` is unchanged: the OpenAI-dialect catch-all is
`vllm_a` with `vllm_b` behind it, and the chart's `VLLM_BASE_URL` / `VLLM_B_BASE_URL`
point at the tailscale egress Service instead of at `host.docker.internal`.

First, prove the path — from inside the cluster, which is the only place it matters:

```bash
kubectl -n headroom logs deploy/headroom-vllm | tail -5
kubectl -n headroom run tailnet-check --rm -it --restart=Never \
  --image=curlimages/curl:8.11.1 -- \
  curl -sS --max-time 10 http://headroom-vllm:8010/v1/models
```

*Expected:* the tailscale log ending in `Startup complete` with a tailnet address, and the
model id back from the second command. If the second times out, §3's firewall check is the
first thing to re-read; `tailscale status` at home should show `headroom-eks-egress` as an
active device.

→ `14-tailnet-path.txt`

**Terminal A — the loop, on the vLLM chain.** Same instrument, same three outcomes, so
"zero dropped" means the same thing here as it did in §8.

```bash
uv run python scripts/load_loop.py \
  --base-url "$GATEWAY" --key "$MOCK_KEY" \
  --model cyankiwi/Qwen3.6-27B-AWQ-INT4 --dialect openai \
  --duration-s 420 --concurrency 2 --interval-ms 1000 --timeout 60 \
  --label "vllm-failover" --out /tmp/p10-failover-loop.json
```

> **`--timeout 60` is not decoration.** The default is 15 seconds, which is sized against a
> mock whose p99 is under 100 ms; non-streamed 27B inference on the operator's 4090s takes
> 12-16 seconds to first token. The first run of this section left the default alone and
> scored **fourteen legitimate completions as `dropped`**, and the failover demo appeared to
> have failed. It had not — run 2 with `--timeout 60` read 92/92 with a GPU killed mid-run.
> Both files are committed, `15a-…-run1-timeout15.json` beside `15-failover-loop.json`,
> because a phase log that keeps only the run that worked is one nobody can check (H-092).
> If you point this loop at anything else, re-derive the number first.

> §7a's `loops` key is minted with **no** `allowed_models`, deliberately, so it can reach
> the vLLM chain here as well as the mock chain in §8. A key scoped to `mock-*` would answer
> 403 to every request in this section — which the loop would score as `dropped`, correctly
> and unhelpfully.

**Terminal B — kill a GPU, about ninety seconds in.**

```bash
docker kill vllm-a
sleep 120
docker start vllm-a          # or the full `docker run` from docs/vllm.md
```

**Terminal C — watch the hops arrive.**

```bash
# `/admin/usage` answers a bare array, not an envelope — `.rows[]` silently prints nothing.
watch -n2 "curl -sS '$GATEWAY/admin/usage?limit=5' -H 'Authorization: Bearer $ADMIN' \
  | jq -c '.[] | {provider, failover_hops, failover_from, failover_error, outcome}'"
curl -sS "$GATEWAY/admin/providers" -H "Authorization: Bearer $ADMIN" | jq
```

*Expected:* rows flip from `{"provider":"vllm_a","failover_hops":0}` to
`{"provider":"vllm_b","failover_hops":1,"failover_from":"vllm_a"}` within one request of
the kill; `/admin/providers` shows `vllm_a` `open` with a cooldown counting down (H-052);
and after the restart, one probe re-admits it and the rows go back. The loop's summary
should read `dropped: 0` — every pre-first-token fault was hidden from the caller, which
is §P8.H3's first clause, measured this time rather than asserted.

→ `15-failover-loop.json`, `16-failover-ledger.json`, `17-provider-health.json`

**And keep §9's port-forward open on a fourth screen while this runs.** The console's *Live
traffic* view is the one place the whole arc is visible at once — the stack's colour moving
from `vllm_a` to `vllm_b`, `CALLER-VISIBLE 5XX` holding at zero, and each flipped row naming
the hop that produced it. Capture it twice: once mid-kill with the breaker `open`, once after
the arc completes. It costs nothing, the window is about fifteen minutes wide, and it is gone
with the cluster.

→ `24-live-flip.png`, `25-breaker-open.png`

## 12. Capture everything else — **before you destroy anything**

`docs/evidence/p10-eks/README.md` is the capture list. Evidence lives in the repo and
outside every blast radius (invariant 9).

```bash
kubectl -n headroom get pods,svc,deploy,hpa,pdb -o wide > docs/evidence/p10-eks/04-pods-svc.txt
kubectl -n headroom get events --sort-by=.lastTimestamp > docs/evidence/p10-eks/18-events.txt
kubectl get nodes -o wide >> docs/evidence/p10-eks/02-nodes.txt
helm history headroom -n headroom > docs/evidence/p10-eks/11-helm-history.txt
cp /tmp/p10-load-loop.json docs/evidence/p10-eks/09-load-loop.json
cp /tmp/p10-failover-loop.json docs/evidence/p10-eks/15-failover-loop.json
```

Everything a `kubectl` can show is gone the moment §14 runs.

## 13. `helm uninstall` — **the load balancer's $0.54/day stops here**

**This runs before `eksctl delete cluster`, and the order is not stylistic.** A
`Service` of type LoadBalancer is deleted by the cloud controller manager, which lives in
the control plane. Delete the cluster first and the NLB is orphaned: it survives, it bills,
and nothing is left that knows it exists. It is the single most common thing an EKS
teardown leaves behind.

```bash
helm uninstall headroom -n headroom
kubectl -n headroom get svc
aws elbv2 describe-load-balancers \
  --query 'LoadBalancers[?VpcId==`'"$(terraform -chdir=deploy/aws/data output -raw vpc_id)"'`].[LoadBalancerName,State.Code]' \
  --output table
```

*Expected:* no services left, and **an empty load-balancer list** — wait for it. If a load
balancer is still listed after a couple of minutes, do not proceed; find out why while the
thing that can delete it still exists.

→ `19-uninstall.txt`

## 14. `eksctl delete cluster` — **$4.40/day stops here**

```bash
eksctl delete cluster -f deploy/k8s/eksctl/cluster.yaml --disable-nodegroup-eviction --wait
```

*Expected:* ten to fifteen minutes, ending in
`all cluster resources were deleted`. eksctl removes both CloudFormation stacks, the OIDC
provider, and the IAM role behind the service account. `--disable-nodegroup-eviction`
because the PodDisruptionBudget the chart installs is gone with §13, and a drain that waits
for a budget nobody is defending is a teardown that hangs.

**It must not touch the VPC**, which belongs to Terraform. eksctl knows: the cluster
config gave it an existing `vpc.id`, so it deletes what it created and leaves the network
alone. §16's `terraform plan` is where that is checked rather than assumed.

## 15. The per-service empty checks — **per service, not the tag scan**

§P9's words, and they apply unchanged: *"per-service empty checks (not the tag scan —
tombstones lie)"*. A resource in a deleting state still carries its tags.

```bash
REGION=us-east-1
VPC=$(terraform -chdir=deploy/aws/data output -raw vpc_id)

# No cluster, no node group
aws eks list-clusters --region $REGION
aws cloudformation list-stacks --region $REGION \
  --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE DELETE_FAILED \
  --query 'StackSummaries[?starts_with(StackName, `eksctl-headroom`)].[StackName,StackStatus]'

# No instances, no auto-scaling group, no orphaned volumes
aws ec2 describe-instances --region $REGION --filters "Name=vpc-id,Values=$VPC" \
  --query 'Reservations[].Instances[?State.Name!=`terminated`].[InstanceId,State.Name]'
aws autoscaling describe-auto-scaling-groups --region $REGION \
  --query 'AutoScalingGroups[?starts_with(AutoScalingGroupName, `eks-`)].AutoScalingGroupName'
aws ec2 describe-volumes --region $REGION --filters Name=status,Values=available \
  --query 'Volumes[].[VolumeId,Size,CreateTime]'

# No load balancers, no target groups — the classic EKS leftover
aws elbv2 describe-load-balancers --region $REGION --query 'LoadBalancers[].LoadBalancerName'
aws elb describe-load-balancers --region $REGION --query 'LoadBalancerDescriptions[].LoadBalancerName'
aws elbv2 describe-target-groups --region $REGION --query 'TargetGroups[].TargetGroupName'

# No leftover ENIs holding the subnets hostage, no security groups eksctl made
aws ec2 describe-network-interfaces --region $REGION --filters "Name=vpc-id,Values=$VPC" \
  --query 'NetworkInterfaces[].[NetworkInterfaceId,Description,Status]' --output table
aws ec2 describe-security-groups --region $REGION --filters "Name=vpc-id,Values=$VPC" \
  --query 'SecurityGroups[].[GroupName,GroupId]' --output table

# No IAM role, no OIDC provider, no log group
aws iam list-roles --query 'Roles[?starts_with(RoleName, `eksctl-headroom`)].RoleName'
aws iam list-open-id-connect-providers
aws logs describe-log-groups --region $REGION \
  --query 'logGroups[?contains(logGroupName, `eks`) || contains(logGroupName, `headroom`)].logGroupName'

# And what SHOULD still be there, until section 16
aws rds describe-db-instances --region $REGION --query 'DBInstances[].DBInstanceIdentifier'
aws dynamodb list-tables --region $REGION
aws ecr describe-repositories --region $REGION --query 'repositories[].repositoryName'
```

*Expected:* every list above the last block empty, except the VPC's own security groups —
`headroom-workload`, `headroom-db` and the VPC's `default` remain, because they are the
data layer's. And `headroom-db`, `headroom_budgets`/`headroom_buckets`, and
`headroom/gateway`/`headroom/ui` for the last block.

Three that are worth knowing about because they are not failures:

- **A leftover ENI in `available` state** can persist for a minute after the node group is
  gone. It is free and it disappears; if §16's VPC destroy ever hangs on
  `DependencyViolation`, this is why — wait and retry.
- **The OIDC provider** is deleted by `eksctl delete cluster`. If it survives, it is free
  and it is also a dangling trust relationship: remove it by hand.
- **An `available` EBS volume** is the one that costs money quietly. There should be none.

→ `20-empty-checks.txt`

## 16. Destroy the data layer — **everything stops here**

This is `deploy/aws/README.md` §12, and this is the phase it belongs to. Written out here
so the teardown is in one place.

```bash
terraform -chdir=deploy/aws/data plan
```

*Expected:* `No changes.` — the cluster came and went without touching the network the
data layer owns. **That is the check that eksctl left the VPC alone**, and it is the same
line Phase 9 ended on.

```bash
terraform -chdir=deploy/aws/data destroy
```

*Expected:* RDS takes several minutes. `force_delete` on the ECR repositories is what lets
this succeed with 810 MiB of images still in them, and `recovery_window_in_days = 0` on the
three secrets is what would let them be re-created under the same names afterwards.

```bash
aws rds describe-db-instances --region $REGION --query 'DBInstances[].DBInstanceIdentifier'
aws rds describe-db-snapshots --region $REGION --query 'DBSnapshots[].DBSnapshotIdentifier'
aws dynamodb list-tables --region $REGION
aws ecr describe-repositories --region $REGION --query 'repositories[].repositoryName'
aws secretsmanager list-secrets --region $REGION --query 'SecretList[].Name'
aws ec2 describe-vpcs --region $REGION --query 'Vpcs[?!IsDefault].VpcId'
```

*Expected:* every one of them empty. The snapshot check earns its place:
`backup_retention_period = 0` and `skip_final_snapshot = true` are the two flags that make
it so, and a snapshot nobody knows about bills for storage long after the instance is gone.

> **RDS's own generated-password secret** (`rds!db-…`) goes to a recovery window rather
> than away. It is free; `aws secretsmanager list-secrets --include-planned-deletion` shows
> it.

→ `21-data-destroy.txt`, `22-final-empty-checks.txt`

## 17. Billing: estimate versus actual — **A7, closed**

The last item, and it arrives last because Cost Explorer needs up to 24 hours after the
final day of usage.

```bash
aws ce get-cost-and-usage \
  --time-period Start=<day 1>,End=<day after teardown> \
  --granularity DAILY --metrics UnblendedCost \
  --filter '{"Tags":{"Key":"Project","Values":["headroom"]}}' \
  --group-by Type=TAG,Key=Layer \
  --output table
```

Then the console view, which is the artifact: **Cost Explorer, filtered to
`Project=headroom`, grouped by `Layer`, daily, across the window.**

→ `23-billing.png`. **`docs/evidence/p9-aws/18-billing.png` was not captured** — by the time
`Layer` was active, Phase 9 was over and there was no split left for it to show. See the
warning below.

Fill in the table in `docs/PHASE_LOG.md`, whichever way it lands. **What this run produced is
in the right-hand column**, so the next person can see both the shape and the trap:

| | Estimate | Actual, 2026-08-11 |
|---|---:|---:|
| EKS control plane | $2.40/day | $1.3533 |
| Nodes (2 x t3.medium) + EBS | $2.11/day | $1.1696 |
| Network Load Balancer | $0.54/day | $0.3380 |
| Data layer (`Layer=data`) | $0.53/day | $0.3527 |
| Not priced above: VPC, Secrets Manager, ECS, ECR | — | $0.3419 |
| **Window total** (14 h, not 3 days) | **$17–19** for 3 days | **$3.5556**, a rate of ≈$6.10/day |
| **A7's pre-registered estimate** | **$20–25** | not run — the window was compressed (H-096) |

**Both outcomes are publishable.** A7 is an assumption in §0.4's register, and a window
that lands at $12 or at $31 is a fact about this architecture either way. What is not
acceptable is a table with an estimate and no actual, which is exactly what Phase 9's
spend line has been carrying since its close.

> ### ⚠️ Read this before you `apply` anything, not when you get here — **H-102**
>
> **This run could not attribute 14% of its own bill, and the tagging was not the reason.**
> Every resource carried all four keys — `default_tags` on both Terraform roots, and
> `metadata.tags` plus the node group's `tags` in `deploy/k8s/eksctl/cluster.yaml`. **72.4%
> of the tagged spend still came back with no `Layer` value.** Three things cause that, and
> all three are decided before the first hourly resource exists:
>
> 1. **Cost allocation tags label line items from activation forward. They are never
>    backfilled.** §1's `update-cost-allocation-tags-status` is not a formality you can
>    retry at leisure — a key that goes `Active` at 16:54 groups **nothing** that was billed
>    that morning. In this run `Project` activated at 02:16 UTC and `Layer`/`Phase`/
>    `ManagedBy` at 16:54 UTC, on the one day that carried the entire bill.
>    **So: seed the keys (§1), poll `list-cost-allocation-tags --status Active` until all
>    four are there, and only then create the first thing that bills by the hour.** The wait
>    is free — the only resource standing is an empty ECR repository. H-080 got the seeding
>    right and treated the wait as a delay; it is not a delay, it is a deadline.
> 2. **Tag what Kubernetes creates for you, not just what you declare.** The NLB is made by
>    the in-cluster cloud controller manager, so neither Terraform nor `cluster.yaml` tags
>    it: it showed **$0.02** of its actual **$0.3380** under `Project=headroom`. If the
>    `Layer` split is meant to cover it, add
>    `service.beta.kubernetes.io/aws-load-balancer-additional-resource-tags` to the Service
>    annotations beside the two already in `values.aws.yaml.example`. **Untested here** — the
>    gap was measured after teardown.
> 3. **If your window is short, read it `--granularity HOURLY` while it is still inside Cost
>    Explorer's 14-day retention.** A DAILY read of a fourteen-hour window cannot tell
>    *billed before activation* from *never tagged*, and once the window ages out you are
>    left inferring which it was — which is exactly what H-102 has to do.
>
> Also worth knowing before you read your own bill: **filter the account's baseline out.**
> This account carried ≈$0.2242/day of pre-existing S3 spend that has nothing to do with
> Headroom, and counting it would have overstated the project by 6%.

---

## Reference — what runs where

| Thing | Where | Why |
|---|---|---|
| Gateway pods | 2 replicas, public subnets, node IPs | a route to Anthropic and ECR with no NAT gateway (H-075); inbound only via the node port the NLB targets |
| Console pod | ClusterIP, reached by `kubectl port-forward` | free, and guarded by IAM and RBAC rather than by an IP allow-list |
| RDS | the data layer's private subnets | the door in is the `headroom-workload` security group, which the node group wears |
| DynamoDB | gateway endpoint, IRSA role | free, and the conditional writes never leave the VPC — the P4/P4b code path unchanged |
| The two vLLM instances | the operator's desk, over tailscale | a pod dials out to a tailnet address; nothing is advertised into the cluster |
| The three secrets | a Kubernetes Secret, created by hand | Terraform made the containers in Phase 9; nothing automated ever reads the values |

**What this cluster deliberately does not have:** an Ingress controller, cert-manager,
Prometheus, metrics-server, an autoscaler, a service mesh, or a GitOps controller. Each is
a defensible thing to run and each would be a second and third Helm release in a
three-day window whose job is to show one application deployed correctly. The HPA template
ships in the chart and ships off, with the two prerequisites for turning it on written
beside it.
