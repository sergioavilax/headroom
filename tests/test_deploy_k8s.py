"""The chart and the cluster config, held to the code they deploy — and to Phase 9.

`tests/test_deploy_aws.py` opens by saying what Terraform's own checks cannot notice: an
agreement between two files that nothing forces to agree. Kubernetes has the same gap and
a wider one, because there are now *three* descriptions of one gateway — `docker-compose.yml`,
`deploy/aws/compute/ecs.tf`, and this chart — and nothing in any toolchain compares them.

The failures this file exists to catch:

* **The chart drifts from the ECS task definition.** A variable added on one side and not
  the other produces a gateway that starts, serves, and behaves differently in one
  environment, silently. `test_the_chart_sets_every_variable_the_ecs_task_definition_sets`
  is the pin.
* **`DYNAMODB_ENDPOINT_URL` gets set "for parity".** Its *absence* is assumption A1's
  entire second half: with no endpoint override, `headroom/db/dynamo.py` resolves the
  regional endpoint and signs with the pod's IRSA role. A well-meaning addition here
  would point a cluster at an emulator that is not there.
* **A pod that cannot be scheduled.** Resource requests are checked by nothing until the
  scheduler quietly leaves a pod `Pending` with no error anywhere — the Kubernetes-shaped
  version of the problem H-082 is about. The arithmetic is done here instead.
* **A name too long for a label.** 63 characters, no exceptions, and the failure names
  `metadata.labels` rather than the release name that caused it.
* **A secret with a home to go to.** The chart declares no `kind: Secret` and
  `values.schema.json` refuses unknown keys, so there is nowhere to write one. Asserted,
  because "there is nowhere" is a property that a single helpful template would end.

Parsing is crude on purpose, in this file's older sibling's house style: substring and
regex assertions over text where the thing being asserted is that two strings are the same
string, and PyYAML where the thing being asserted is arithmetic.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from headroom.api.drain import DRAIN_FILE_ENV
from headroom.db.dynamo import (
    DEFAULT_BUCKETS_TABLE,
    DEFAULT_BUDGETS_TABLE,
    DYNAMODB_ENDPOINT_ENV,
)

REPO = Path(__file__).resolve().parents[1]
K8S = REPO / "deploy" / "k8s"
CHART = K8S / "headroom"
TEMPLATES = CHART / "templates"
AWS = REPO / "deploy" / "aws"

RUNBOOK = (K8S / "README.md").read_text(encoding="utf-8")
CHART_YAML: dict[str, Any] = yaml.safe_load((CHART / "Chart.yaml").read_text(encoding="utf-8"))
VALUES: dict[str, Any] = yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8"))
SCHEMA: dict[str, Any] = json.loads((CHART / "values.schema.json").read_text(encoding="utf-8"))
CLUSTER: dict[str, Any] = yaml.safe_load(
    (K8S / "eksctl" / "cluster.yaml").read_text(encoding="utf-8")
)
ECS_TF = (AWS / "compute" / "ecs.tf").read_text(encoding="utf-8")
IAM_TF = (AWS / "compute" / "iam.tf").read_text(encoding="utf-8")
NETWORK_TF = (AWS / "data" / "network.tf").read_text(encoding="utf-8")
GENERATOR = (K8S / "render_config.py").read_text(encoding="utf-8")


def template_text() -> str:
    """Every file under `templates/`, concatenated. One haystack for the chart."""
    return "\n".join(
        sorted(path.read_text(encoding="utf-8") for path in TEMPLATES.iterdir() if path.is_file())
    )


TEMPLATE_TEXT = template_text()


#: Files under `deploy/k8s/` whose *text* is the deliverable. A suffix list rather than
#: "every file", because importing the config generator leaves a `__pycache__` behind and
#: a bytecode file is not a place a credential can hide in a form a regex would find.
TEXT_SUFFIXES = frozenset({".yaml", ".yml", ".json", ".md", ".py", ".tpl", ".txt", ".example"})


def text_files() -> list[Path]:
    return [
        path
        for path in sorted(K8S.rglob("*"))
        if path.is_file() and (path.suffix in TEXT_SUFFIXES or path.name == ".helmignore")
    ]


def code_only(source: str) -> str:
    """The same text with comments removed — line comments and Helm's block form.

    Necessary for the same reason `test_deploy_aws.py` needs it: these files carry long
    comments *about* the things being asserted — "note what is NOT here:
    `DYNAMODB_ENDPOINT_URL`", and a three-row table comparing compose, ECS and EKS — so a
    bare substring search finds the prose explaining a rule and reports it as a violation
    of it.

    `{{/* … */}}` is stripped as a block rather than line by line, because that is what it
    is: Helm removes it before rendering, so nothing inside it can reach a manifest.
    """
    without_blocks = re.sub(r"\{\{/\*.*?\*/\}\}", "", source, flags=re.DOTALL)
    return "\n".join(
        line for line in without_blocks.splitlines() if not line.lstrip().startswith("#")
    )


TEMPLATE_CODE = code_only(TEMPLATE_TEXT)


# --- the chart is a chart -----------------------------------------------------------------


def test_the_chart_is_shaped_the_way_helm_expects() -> None:
    assert CHART_YAML["apiVersion"] == "v2"
    assert CHART_YAML["name"] == CHART.name, "helm wants the directory named for the chart"
    assert CHART_YAML["kubeVersion"], (
        "no floor on the cluster version: a chart that used `policy/v1` on a cluster too "
        "old fails on a missing kind rather than on a version"
    )
    for expected in ("gateway-deployment.yaml", "gateway-service.yaml", "_helpers.tpl"):
        assert (TEMPLATES / expected).exists()


def test_the_chart_says_the_version_the_gateway_says() -> None:
    """`appVersion` is what is inside the image, so it tracks `pyproject.toml` rather than
    the chart's own version — which moves when a probe timeout changes and no gateway has."""
    pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    [version] = re.findall(r'^version = "([^"]+)"', pyproject, re.MULTILINE)
    assert CHART_YAML["appVersion"] == version


# --- parity with the ECS task definition ----------------------------------------------------


def ecs_environment_names() -> set[str]:
    """The names in `local.gateway_environment` in `deploy/aws/compute/ecs.tf`."""
    [block] = re.findall(r"gateway_environment = \[(.*?)\n  \]", ECS_TF, re.DOTALL)
    names = set(re.findall(r'\{ name = "(\w+)"', block))
    assert names, "parsed no environment names out of ecs.tf — did the file's shape change?"
    return names


def ecs_secret_names() -> set[str]:
    """The names in the gateway container's `secrets` block."""
    [block] = re.findall(r"secrets = \[(.*?)\n      \]", ECS_TF, re.DOTALL)
    names = set(re.findall(r'\{ name = "(\w+)"', block))
    assert len(names) == 3, f"expected three secrets in the task definition, parsed {names}"
    return names


def test_the_chart_sets_every_variable_the_ecs_task_definition_sets() -> None:
    """The compose-parity claim, as an assertion.

    §P10: "the chart's environment must be recognizably the same gateway". Recognisable is
    not a thing a reader can check across two hundred lines of HCL and a Helm template, so
    it is checked here: every name Phase 9 hands the gateway, this chart hands it too.
    """
    for name in ecs_environment_names() | ecs_secret_names():
        assert f"name: {name}" in TEMPLATE_CODE, (
            f"the ECS task definition sets {name} and this chart does not: the two "
            f"environments would be running the same image with different configuration"
        )


def test_the_three_secrets_arrive_by_reference_and_never_as_a_value() -> None:
    """`valueFrom.secretKeyRef` is the Kubernetes equivalent of the task definition's
    `secrets` block, and it has the same property: the value is not in the object.

    Every occurrence is checked, not the first: `DATABASE_URL` is set twice — once for the
    gateway and once for the migration Job — and a rule that only ever looked at one of
    them would miss the one somebody adds later.
    """
    for name in ecs_secret_names():
        blocks = re.findall(rf"- name: {name}\n(.*?)(?=\n *- name:|\Z)", TEMPLATE_CODE, re.DOTALL)
        assert blocks, f"{name} is set nowhere in the chart"
        for block in blocks:
            assert "secretKeyRef" in block, f"{name} is not read from a Secret"
            assert "value:" not in block.split("valueFrom")[0], f"{name} carries a literal value"


def test_the_deployed_gateway_is_not_told_about_an_emulator() -> None:
    """Assumption A1's second half, on its third runtime. The line that is *absent* is
    what makes `headroom/db/dynamo.py` resolve the regional endpoint and sign with the
    pod's IRSA role rather than with an emulator's dummy credential.

    Read with comments stripped, and skipping the runbook: prose is allowed to explain
    which variable is deliberately not set, and the check is about what renders.
    """
    for path in text_files():
        if path.suffix == ".md":
            continue
        assert DYNAMODB_ENDPOINT_ENV not in code_only(path.read_text(encoding="utf-8")), path


def test_the_dynamodb_tables_are_the_names_the_gateway_defaults_to() -> None:
    assert VALUES["aws"]["budgetsTable"] == DEFAULT_BUDGETS_TABLE
    assert VALUES["aws"]["bucketsTable"] == DEFAULT_BUCKETS_TABLE


def test_the_ports_are_the_ports_every_other_environment_uses() -> None:
    """H-006's numbers, three deployments later. An operator who has typed
    `localhost:8080` for ten phases should not learn a second number for the cluster."""
    compose = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    assert "${GATEWAY_PORT:-8080}:8000" in compose
    assert "${UI_PORT:-3001}:3000" in compose

    assert VALUES["gateway"]["containerPort"] == 8000
    assert VALUES["gateway"]["service"]["port"] == 8080
    assert VALUES["ui"]["containerPort"] == 3000
    assert VALUES["ui"]["service"]["port"] == 3001

    # And the same two numbers the Phase 9 ALB listened on.
    variables = (AWS / "compute" / "variables.tf").read_text(encoding="utf-8")
    assert re.search(r'variable "gateway_port".*?default\s*=\s*8080', variables, re.DOTALL)
    assert re.search(r'variable "ui_port".*?default\s*=\s*3001', variables, re.DOTALL)


def test_the_probes_name_endpoints_the_two_apps_actually_serve() -> None:
    """A probe pointed at a path that does not exist is a pod that never becomes Ready,
    diagnosed from an event rather than from anything red."""
    assert '@app.get("/healthz")' in (REPO / "headroom" / "api" / "main.py").read_text(
        encoding="utf-8"
    )
    assert (REPO / "ui" / "app" / "api" / "healthz" / "route.ts").exists()

    gateway = code_only((TEMPLATES / "gateway-deployment.yaml").read_text(encoding="utf-8"))
    assert gateway.count("path: /healthz") == 3, "startup, readiness and liveness"
    ui = code_only((TEMPLATES / "ui-deployment.yaml").read_text(encoding="utf-8"))
    assert ui.count("path: /api/healthz") == 2


# --- no secret has anywhere to go ------------------------------------------------------------


def test_the_chart_declares_no_secret_and_has_nowhere_to_put_one() -> None:
    """Invariant 3, structurally. `deploy/aws/data` creates secret *containers* and never
    a version (H-077); the equivalent here is stronger — the chart creates nothing at all,
    so a value cannot reach a values file, a `--set`, or a rendered manifest."""
    assert "kind: Secret" not in TEMPLATE_CODE
    assert "stringData" not in TEMPLATE_CODE
    properties = SCHEMA["properties"]["secrets"]["properties"]
    assert set(properties) == {"existingSecret", "keys"}, (
        "the `secrets` block gained a field: if it can hold a value, invariant 3 is now a "
        "convention rather than a property"
    )
    assert SCHEMA["additionalProperties"] is False


@pytest.mark.parametrize(
    "pattern",
    [
        r"sk-ant-[A-Za-z0-9_-]{16,}",
        r"AKIA[0-9A-Z]{16}",
        r"hk_[A-Za-z0-9_-]{20,}",
        # An ephemeral tailscale auth key. It is the one credential this phase introduces
        # and it goes in a Secret by hand, exactly like the other three.
        r"tskey-[A-Za-z0-9-]{10,}",
    ],
)
def test_no_credential_shaped_string_is_committed_under_deploy_k8s(pattern: str) -> None:
    for path in text_files():
        assert not re.search(pattern, path.read_text(encoding="utf-8")), path


def test_git_keeps_the_values_example_and_not_the_real_one() -> None:
    """`values.aws.yaml` carries the operator's home CIDR, so it is out of git for exactly
    the reason `terraform.tfvars` is. Asked of git rather than of the filesystem — the
    lesson `test_the_only_tfvars_git_keeps_is_the_example` learned the hard way, on the one
    machine that had actually run the runbook."""
    gitignore = (REPO / ".gitignore").read_text(encoding="utf-8")
    assert "deploy/k8s/values.*.yaml" in gitignore
    assert "!deploy/k8s/values.*.yaml.example" in gitignore
    assert (K8S / "values.aws.yaml.example").exists()

    tracked = subprocess.run(
        ["git", "ls-files", "--", "deploy/k8s/values.*.yaml"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    if tracked.returncode != 0:
        pytest.skip("not a git work tree, or git is not installed")
    kept = [name for name in tracked.stdout.split() if not name.endswith(".example")]
    assert not kept, f"git is tracking a real values file: {kept}"


# --- the load balancer is locked, and cannot be published by accident -------------------------


def test_a_load_balancer_cannot_be_rendered_without_source_ranges() -> None:
    """`var.home_cidr` has no default and refuses `0.0.0.0/0`; this is that refusal one
    runtime over, and it fires at `helm template` rather than at a load balancer that
    already exists."""
    helpers = code_only((TEMPLATES / "_helpers.tpl").read_text(encoding="utf-8"))
    assert "headroom.requireSourceRanges" in helpers
    assert "fail (printf" in helpers
    for name in ("gateway-service.yaml", "ui-service.yaml"):
        # `code_only`, and the sabotage run is why. This assertion first read the raw file
        # and passed with the guard deleted, because the comment three lines below the
        # `loadBalancerSourceRanges` block says "see `headroom.requireSourceRanges`" — so
        # the test was finding the prose about the rule and reporting it as the rule.
        # H-072's lesson, caught by breaking the thing rather than by reading the test.
        text = code_only((TEMPLATES / name).read_text(encoding="utf-8"))
        assert "headroom.requireSourceRanges" in text, f"{name} can publish itself unguarded"

    assert VALUES["gateway"]["service"]["type"] == "ClusterIP", (
        "the shipped default reaches nothing outside the cluster; AWS is a values file"
    )
    assert VALUES["gateway"]["service"]["loadBalancerSourceRanges"] == []


def test_the_defaults_name_no_cloud_at_all() -> None:
    """`helm template` with no `-f` has to render on any cluster and reach nothing. That is
    what lets CI lint and render this chart keylessly, and it is why every AWS-specific
    value is generated into a gitignored file instead."""
    defaults = (CHART / "values.yaml").read_text(encoding="utf-8")
    for cloud in ("amazonaws.com", "arn:aws:", "eks.amazonaws.com"):
        assert cloud not in code_only(defaults), f"a default names {cloud}"


# --- the rollout drops nothing ----------------------------------------------------------------


def test_the_rollout_never_takes_the_last_ready_pod_away() -> None:
    """The two numbers "zero dropped requests" actually rests on, plus the three things
    that make them true in practice: more than one replica, a readiness probe the Service
    reads, and a pause before SIGTERM so kube-proxy has caught up."""
    assert VALUES["gateway"]["strategy"]["maxUnavailable"] == 0
    assert VALUES["gateway"]["strategy"]["maxSurge"] >= 1
    assert VALUES["gateway"]["replicaCount"] >= 2, (
        "one replica with maxUnavailable 0 still has a window with no Ready pod behind "
        "the Service, because the replacement has to become Ready before the old one goes"
    )
    assert VALUES["gateway"]["lifecycle"]["preStopSleepSeconds"] >= 1
    assert (
        VALUES["gateway"]["terminationGracePeriodSeconds"]
        > VALUES["gateway"]["lifecycle"]["preStopSleepSeconds"]
    ), "the grace period must outlast the preStop sleep or SIGKILL arrives during it"

    deployment = code_only((TEMPLATES / "gateway-deployment.yaml").read_text(encoding="utf-8"))
    assert "readinessProbe" in deployment
    assert "preStop" in deployment


def test_the_prestop_hook_drains_before_it_sleeps() -> None:
    """§8's finding, pinned in the order it has to happen in.

    The sleep covers the *new connection* race while kube-proxy catches up. It has no
    reach over a connection that already exists — conntrack pins an established flow to
    the pod it was given to — so a client with keep-alive connections spends the whole
    sleep talking to the pod that is about to stop, and loses whatever it had written when
    uvicorn closes them. Two runs measured exactly one drop per replaced pod and tripling
    the sleep changed nothing (docs/DECISIONS.md H-091).

    The `touch` is what fixes it, and it has to come *first*: the sleep is the window in
    which clients read `Connection: close` and retire those connections themselves. A hook
    that slept and then touched would drain nothing at all and look identical in a diff.
    """
    hook = code_only((TEMPLATES / "gateway-deployment.yaml").read_text(encoding="utf-8"))
    hook = hook[hook.index("preStop") :]
    touch = hook.index("touch")
    sleep = hook.index("sleep")
    assert touch < sleep, "preStop must touch the sentinel before it sleeps, not after"
    assert ".Values.gateway.lifecycle.drainFilePath" in hook[touch:sleep]


def test_the_hook_and_the_container_name_the_same_sentinel() -> None:
    """The failure this exists to catch has no symptom: a pod whose hook writes one path
    and whose gateway watches another drains nothing, logs nothing, and reports a healthy
    rollout that quietly drops a request per pod. One values key feeds both, and this is
    the test that notices if that ever becomes two."""
    assert VALUES["gateway"]["lifecycle"]["drainFilePath"].startswith("/")

    helpers = code_only((TEMPLATES / "_helpers.tpl").read_text(encoding="utf-8"))
    env = re.search(r"- name: HEADROOM_DRAIN_FILE\s*\n\s*value: (?P<value>.+)", helpers)
    assert env is not None, "the gateway's environment has to carry the sentinel path"
    assert ".Values.gateway.lifecycle.drainFilePath" in env.group("value")

    deployment = code_only((TEMPLATES / "gateway-deployment.yaml").read_text(encoding="utf-8"))
    assert ".Values.gateway.lifecycle.drainFilePath" in deployment

    assert DRAIN_FILE_ENV == "HEADROOM_DRAIN_FILE", (
        "the chart spells the variable the code reads; renaming one renames both"
    )


def test_compose_and_the_chart_agree_that_the_gateway_can_drain() -> None:
    """The drain is the one piece of Phase 10 that lives in the application rather than in
    the chart, so it is the one piece that can be exercised on a laptop — which is what
    `scripts/rollout_repro.sh` does, and what it needs this variable set for."""
    compose = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    assert DRAIN_FILE_ENV in compose


def test_the_service_keeps_the_load_balancers_targets_still_while_pods_move() -> None:
    """`externalTrafficPolicy: Cluster`. With instance targets the load balancer's targets
    are the nodes, so replacing a pod changes an Endpoints object and nothing about the
    load balancer — no registration, no deregistration delay, no window where it is still
    sending traffic to something Kubernetes has given up on."""
    service = (TEMPLATES / "gateway-service.yaml").read_text(encoding="utf-8")
    assert "externalTrafficPolicy: Cluster" in code_only(service)


def test_a_disruption_budget_protects_what_the_rollout_strategy_cannot() -> None:
    assert VALUES["gateway"]["podDisruptionBudget"]["enabled"] is True
    assert VALUES["gateway"]["podDisruptionBudget"]["minAvailable"] == 1


def test_the_hpa_ships_off_and_does_not_fight_helm_over_the_replica_count() -> None:
    """§P10 asks for "HPA optional-off". Off, and the interaction that makes an enabled one
    behave: when the HPA owns the replica count, the Deployment must not also declare it —
    otherwise every `helm upgrade` scales the fleet back down before the HPA scales it up."""
    assert VALUES["autoscaling"]["enabled"] is False
    hpa = (TEMPLATES / "gateway-hpa.yaml").read_text(encoding="utf-8")
    assert hpa.lstrip().startswith("{{- if .Values.autoscaling.enabled }}")
    deployment = code_only((TEMPLATES / "gateway-deployment.yaml").read_text(encoding="utf-8"))
    assert "{{- if not .Values.autoscaling.enabled }}\n  replicas:" in deployment


# --- names fit, and pods fit ------------------------------------------------------------------

#: The longest suffix any template appends to `headroom.fullname`.
NAME_SUFFIXES = ("-gateway", "-migrate", "-vllm", "-ui")


def test_no_rendered_name_can_exceed_the_63_character_label_limit() -> None:
    """A Kubernetes object name is 63 characters and a *label value* is 63 with no
    exception at all, so a long release name produces an object that fails to apply with a
    message about `metadata.labels` — several layers from the thing that caused it.

    Checked as arithmetic over the helper rather than by rendering, so it holds in the
    keyless suite: the truncation in `headroom.fullname` plus the longest suffix any
    template appends must land on 63 or under.
    """
    helpers = (TEMPLATES / "_helpers.tpl").read_text(encoding="utf-8")
    truncations = {int(value) for value in re.findall(r"trunc (\d+)", helpers)}
    fullname_block = helpers.split('define "headroom.fullname"')[1].split("{{- end -}}")[0]
    fullname_trunc = max(int(value) for value in re.findall(r"trunc (\d+)", fullname_block))
    longest = max(len(suffix) for suffix in NAME_SUFFIXES)

    assert fullname_trunc + longest <= 63, (
        f"headroom.fullname truncates to {fullname_trunc} and the longest suffix is "
        f"{longest}: a release name at the limit renders a {fullname_trunc + longest}-"
        f"character object name, which Kubernetes refuses"
    )
    assert 63 in truncations, "headroom.name is not bounded at all"

    for suffix in NAME_SUFFIXES:
        assert f'"headroom.fullname" . }}}}{suffix}' in TEMPLATE_CODE, (
            f"{suffix} is in this test's list and in no template, or is spelled "
            f"differently there — the arithmetic above is then about the wrong suffixes"
        )


#: `t3.medium`: 2 vCPU, 4 GiB. What a node will actually schedule is less, and by more than
#: people expect — so the two numbers below are deliberately pessimistic rather than
#: nominal. EKS reserves memory as `255Mi + 11Mi x max_pods` (17 on this instance with the
#: VPC CNI) plus a 100Mi eviction threshold, and reserves CPU on a tapering scale.
#: Measured allocatable on a t3.medium sits near 1930m and 3350Mi; these are under both.
NODE_ALLOCATABLE_MILLICORES = 1800
NODE_ALLOCATABLE_MIB = 3300

#: What is on every node before this chart arrives: the VPC CNI and kube-proxy DaemonSets.
#: CoreDNS is a two-replica Deployment, so it is counted once per node as well — the
#: scheduler is free to put both on one node and this is the pessimistic reading.
SYSTEM_MILLICORES_PER_NODE = 25 + 100 + 100
SYSTEM_MIB_PER_NODE = 70


def millicores(quantity: str | int | float) -> int:
    text = str(quantity)
    return int(text[:-1]) if text.endswith("m") else int(float(text) * 1000)


def mebibytes(quantity: str) -> int:
    if quantity.endswith("Mi"):
        return int(quantity[:-2])
    if quantity.endswith("Gi"):
        return int(quantity[:-2]) * 1024
    raise AssertionError(f"unhandled memory quantity {quantity!r}")


def test_every_pod_this_chart_schedules_fits_on_the_node_group_it_targets() -> None:
    """A Deployment whose pods do not fit does not fail. It sits in `Pending`, with the
    reason in a scheduler event and nothing red anywhere — which is H-082's problem
    (nothing local enforces it) wearing Kubernetes clothes.

    Counted at the moment of maximum demand: a rolling upgrade, so `replicaCount + maxSurge`
    gateway pods, plus the console, plus the tailscale egress proxy, plus what the cluster
    is already running.
    """
    node_group = CLUSTER["managedNodeGroups"][0]
    nodes = int(node_group["desiredCapacity"])
    assert node_group["instanceType"] == "t3.medium", (
        "the two allocatable constants in this file are t3.medium's; a different instance "
        "type needs different numbers, not a different assertion"
    )

    gateway_pods = VALUES["gateway"]["replicaCount"] + VALUES["gateway"]["strategy"]["maxSurge"]
    demand = [
        (gateway_pods, VALUES["gateway"]["resources"]["requests"]),
        (VALUES["ui"]["replicaCount"], VALUES["ui"]["resources"]["requests"]),
        (1, VALUES["vllm"]["resources"]["requests"]),
    ]
    cpu = sum(count * millicores(spec["cpu"]) for count, spec in demand)
    memory = sum(count * mebibytes(spec["memory"]) for count, spec in demand)

    cpu_available = nodes * (NODE_ALLOCATABLE_MILLICORES - SYSTEM_MILLICORES_PER_NODE)
    memory_available = nodes * (NODE_ALLOCATABLE_MIB - SYSTEM_MIB_PER_NODE)
    assert cpu <= cpu_available, f"{cpu}m requested, {cpu_available}m schedulable on {nodes} nodes"
    assert memory <= memory_available, f"{memory}Mi requested, {memory_available}Mi schedulable"

    # And the surge pod must have somewhere to *land*, not merely somewhere in aggregate:
    # anti-affinity is `preferred`, so during an upgrade one node carries two gateway pods.
    per_node = 2 * mebibytes(VALUES["gateway"]["resources"]["requests"]["memory"])
    assert per_node <= NODE_ALLOCATABLE_MIB - SYSTEM_MIB_PER_NODE, (
        "two gateway pods do not fit on one node, so a rolling upgrade on a two-node "
        "cluster deadlocks with the surge pod Pending"
    )


def test_the_gateway_may_burst_beyond_what_it_reserves() -> None:
    """The gap between requests and limits is deliberate and load-bearing: the baseline
    gateway is a couple of hundred megabytes, and the CPU torch model behind the semantic
    cache is most of a gigabyte the first time a tenant asks for it (BUILD_PLAN L6).
    Requesting the burst would mean paying for nodes sized for a feature this demo never
    turns on."""
    requests = VALUES["gateway"]["resources"]["requests"]
    limits = VALUES["gateway"]["resources"]["limits"]
    assert mebibytes(limits["memory"]) >= 2 * mebibytes(requests["memory"])
    assert mebibytes(limits["memory"]) >= 1024, "too small for bge-small on CPU torch"


# --- the chart, the cluster config, and the data layer agree ----------------------------------


def test_every_data_layer_output_the_k8s_config_reads_is_one_the_data_layer_publishes() -> None:
    """The keyless half of `test_compute_reads_the_data_layer_through_its_published_outputs`.
    Terraform checks that wiring for `deploy/aws/compute`; a Helm chart and an eksctl
    config have no such mechanism, so the generator's own list is checked against the
    data root's `output` blocks."""
    [block] = re.findall(r"REQUIRED_OUTPUTS = \((.*?)\)", GENERATOR, re.DOTALL)
    names = re.findall(r'"(\w+)"', block)
    assert len(names) > 5, "parsed almost nothing out of REQUIRED_OUTPUTS"

    outputs = (AWS / "data" / "outputs.tf").read_text(encoding="utf-8")
    for name in names:
        assert f'output "{name}"' in outputs, (
            f"the k8s config reads an output the data layer never made: {name}"
        )


def test_the_subnet_tag_names_the_cluster_the_eksctl_config_creates() -> None:
    """A `kubernetes.io/cluster/<name>` tag naming a cluster that does not exist is a tag
    that does nothing, and says nothing about having done nothing. The other tag is what a
    `Service` of type LoadBalancer uses to find a public subnet; without it `EXTERNAL-IP`
    stays `<pending>` and the reason is only in a `kubectl describe` event."""
    variables = (AWS / "data" / "variables.tf").read_text(encoding="utf-8")
    [block] = re.findall(r'variable "eks_cluster_name" \{(.*?)\n\}', variables, re.DOTALL)
    [default] = re.findall(r'default\s*=\s*"([^"]+)"', block)
    assert CLUSTER["metadata"]["name"] == default

    assert '"kubernetes.io/cluster/${var.eks_cluster_name}" = "shared"' in NETWORK_TF
    assert '"kubernetes.io/role/elb"' in NETWORK_TF


def test_the_cluster_is_in_the_data_layers_vpc_with_no_nat_to_hide_behind() -> None:
    """H-075 inherited: there is no NAT gateway, so a node in a private subnet could not
    pull an image or reach the EKS API. Public subnets and public IPs, exactly as the
    Phase 9 Fargate tasks."""
    assert CLUSTER["vpc"]["id"].startswith("vpc-")
    assert set(CLUSTER["vpc"]["subnets"]) == {"public"}, (
        "private subnets have no default route in this VPC; handing them to eksctl would "
        "give the control plane ENIs a home nothing can route out of"
    )
    node_group = CLUSTER["managedNodeGroups"][0]
    assert node_group["privateNetworking"] is False
    [attached] = node_group["securityGroups"]["attachIDs"]
    assert attached.startswith("sg-")
    assert "workload_security_group_id=out[" in GENERATOR.replace(" ", ""), (
        "the generator no longer fills the node group's security group from the data "
        "layer's workload group, which is the only reason a pod can reach RDS"
    )


def test_the_pods_iam_role_grants_what_the_ecs_task_role_granted_and_nothing_more() -> None:
    """IRSA is the EKS spelling of `aws_iam_role.gateway_task`. Same two tables, same
    actions — a cluster that granted more would be a cluster where "the code path is
    unchanged" had quietly stopped being checkable."""
    [service_account] = CLUSTER["iam"]["serviceAccounts"]
    assert service_account["metadata"]["name"] == VALUES["serviceAccount"]["name"]
    assert VALUES["serviceAccount"]["create"] is False, (
        "eksctl creates this service account with its role annotation; a second one from "
        "Helm would overwrite the annotation and every DynamoDB call would lose its role"
    )

    granted: set[str] = set()
    for statement in service_account["attachPolicy"]["Statement"]:
        assert statement["Effect"] == "Allow"
        granted.update(statement["Action"])
        for resource in statement["Resource"]:
            assert resource.startswith("arn:aws:dynamodb:"), f"IRSA grants {resource}"

    ecs_granted = set(re.findall(r'"(dynamodb:\w+)"', IAM_TF))
    assert granted == ecs_granted, (
        f"the cluster's role and the ECS task role disagree: "
        f"only on EKS {sorted(granted - ecs_granted)}, only on ECS {sorted(ecs_granted - granted)}"
    )


def test_the_control_plane_writes_no_logs_nobody_reads() -> None:
    """Five log streams at CloudWatch ingest rates for three days, to answer questions this
    phase does not ask. Explicitly empty rather than absent, so the omission is a decision."""
    assert CLUSTER["cloudWatch"]["clusterLogging"]["enableTypes"] == []


def test_the_cluster_carries_the_cost_allocation_tags_both_terraform_roots_carry() -> None:
    """A7's estimate-versus-actual table needs `Layer` to separate the cluster from the
    data layer it is borrowing. A cluster tagged differently from the two roots would drop
    out of every figure this phase quotes."""
    for tags in (CLUSTER["metadata"]["tags"], CLUSTER["managedNodeGroups"][0]["tags"]):
        assert set(tags) == {"Project", "Layer", "Phase", "ManagedBy"}
        assert tags["Layer"] == "compute", "the cluster is the ephemeral half"
        assert tags["Phase"] == "p10"


# --- the values file and its schema stay in step ----------------------------------------------


def test_the_schema_knows_every_value_the_chart_ships() -> None:
    """`additionalProperties: false` is the point of `values.schema.json` and it is also
    its trap: a value added to `values.yaml` without a matching schema entry makes helm
    refuse to render the chart at all. Caught here rather than at `helm install`."""
    unknown_top_level = sorted(set(VALUES) - set(SCHEMA["properties"]))
    assert not unknown_top_level, f"values.yaml has keys the schema forbids: {unknown_top_level}"
    for name, block in SCHEMA["properties"].items():
        if block.get("additionalProperties") is False and isinstance(VALUES.get(name), dict):
            unknown = set(VALUES[name]) - set(block.get("properties", {}))
            assert not unknown, f"values.{name} has keys the schema forbids: {sorted(unknown)}"


# --- the runbook says what a stranger would need ------------------------------------------------


def test_the_runbook_is_a_three_day_window_with_a_teardown_at_the_end() -> None:
    """§P10's own shape: "day 1 = create + deploy + smoke; days 2-3 = the demos and
    captures spread so the window is real; final day = teardown + empty checks"."""
    for day in ("Day 1", "Day 2", "Day 3"):
        assert day in RUNBOOK, f"the runbook has no {day}"
    assert "eksctl delete cluster" in RUNBOOK
    assert "helm uninstall" in RUNBOOK
    assert RUNBOOK.index("helm uninstall") < RUNBOOK.index("eksctl delete cluster"), (
        "the cluster must not be deleted while a LoadBalancer Service still exists: the "
        "load balancer is orphaned and bills with nothing left to point at it"
    )


def test_the_runbook_names_the_region_the_aws_commands_need() -> None:
    """H-083, carried forward rather than re-learned. The operator's Phase 9 run lost an
    evening to a CLI profile still defaulting to another project's region, and this runbook
    mixes `eksctl`, `kubectl`, `helm` and `aws` — four tools, three of which resolve a
    region from somewhere different."""
    prerequisites, _, _ = RUNBOOK.partition("# Day 1")
    assert prerequisites != RUNBOOK, "the runbook has no Day 1 heading to split on"
    assert "AWS_DEFAULT_REGION" in prerequisites
    assert "ResourceNotFoundException" in prerequisites, "the failure signature is not named"


def test_the_runbook_states_a_cost_before_every_paid_step() -> None:
    """§0.6 discipline, and A7's line is $20-25 for this window. The four things that
    dominate it have to be named, not summarised."""
    for line in ("control plane", "t3.medium", "Load Balancer", "data layer"):
        assert line in RUNBOOK, f"the cost table never mentions {line}"
    assert "$0.10" in RUNBOOK, "the EKS control plane's own hourly rate is the headline"
    assert "per day" in RUNBOOK


def test_the_runbook_picks_up_the_two_things_phase_9_left_open() -> None:
    """The P9 close-out deferred `02-cost-allocation-tags.png` and `18-billing.png` on
    Billing's tag-key discovery, and H-080 as amended puts the retry "at the start of
    Phase 10's first session, before the cluster exists". A carried item with no home in
    the next runbook is a carried item that never lands."""
    _, _, day_one = RUNBOOK.partition("# Day 1")
    warm_up, _, rest = day_one.partition("## 2.")
    assert rest, "Day 1 has no second step, so there is no first step to check"
    assert "update-cost-allocation-tags-status" in warm_up, (
        "the tag activation retry is not the first thing Day 1 does, which is where "
        "H-080 as amended puts it — 'before the cluster exists'"
    )
    assert "02-cost-allocation-tags" in warm_up
    assert "18-billing" in RUNBOOK
    assert "H-080" in RUNBOOK


def test_the_runbook_tears_the_data_layer_down_at_the_end_of_this_phase() -> None:
    """`deploy/aws/README.md` section 12 has said so since Phase 9: the data layer is the
    thing still accruing after the cluster is gone, and this is the phase that owns it."""
    assert "deploy/aws/README.md" in RUNBOOK, "the Phase 9 runbook's own section 12 is unnamed"
    assert "terraform -chdir=deploy/aws/data destroy" in RUNBOOK
    for survivor in ("describe-db-snapshots", "list-secrets", "describe-vpcs"):
        assert survivor in RUNBOOK, (
            f"the final empty checks never ask about {survivor}: a snapshot nobody knows "
            f"about bills for storage long after the instance it came from is gone"
        )


def test_the_evidence_list_exists_and_carries_phase_nines_two_open_items() -> None:
    path = REPO / "docs" / "evidence" / "p10-eks" / "README.md"
    capture_list = path.read_text(encoding="utf-8")
    assert "02-cost-allocation-tags" in capture_list
    assert "18-billing" in capture_list
    assert "p9-aws" in capture_list, "the two carried items belong to Phase 9's directory"
