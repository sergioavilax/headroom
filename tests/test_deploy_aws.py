"""The deployment, held to the code it deploys.

Terraform has its own checks — `fmt`, `validate`, and the plan the operator reads before
every apply (`make tf-check`, and a CI job that runs it keylessly). None of them can
notice the failures that actually happen here, because all of them are agreements between
two files that nothing forces to agree:

* An alarm's log filter names `$.budget_status`. Rename that field in
  `headroom/core/context.py` and the alarm keeps applying cleanly, keeps reporting OK, and
  never fires again. Nothing goes red anywhere.
* A task definition names `headroom_budgets`. Change `DEFAULT_BUDGETS_TABLE` and the
  gateway creates its own empty table beside the one Terraform made, and the budget gate
  starts from zero.
* The Lambda's environment names `DATABASE_URL_SECRET_ARN`. Rename the constant the
  handler reads and the nightly job falls through to a compose-shaped default, in a VPC
  where nothing is listening on it.

That is H-072's lesson — *a reader tested against a fixture its own author invented can
only ever confirm the reader* — pointed at infrastructure instead of at a runbook's SQL.
Every test below reads the real `.tf` file and holds it to a real Python constant.

The parsing is deliberately crude: these are substring and regex assertions over the
files as text, not an HCL parse. A parser would be a dependency, and the thing being
asserted is that two strings are the same string.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from headroom.core.context import RequestContext
from headroom.db.dynamo import (
    DEFAULT_BUCKETS_TABLE,
    DEFAULT_BUDGETS_TABLE,
    DYNAMODB_ENDPOINT_ENV,
)
from headroom.rollup.handler import DATABASE_URL_SECRET_ARN_ENV

REPO = Path(__file__).resolve().parents[1]
DEPLOY = REPO / "deploy" / "aws"
DATA = DEPLOY / "data"
COMPUTE = DEPLOY / "compute"
RUNBOOK = (DEPLOY / "README.md").read_text(encoding="utf-8")


def tf(root: Path) -> str:
    """Every `.tf` file in a root, concatenated. One haystack per layer."""
    return "\n".join(sorted(path.read_text(encoding="utf-8") for path in root.glob("*.tf")))


def code_only(source: str) -> str:
    """The same text with whole-line ``#`` comments removed.

    Necessary, and the first run of this file is why. These `.tf` files carry long
    comments *about* the things being asserted — "a `password` argument puts the literal
    in state", "`DYNAMODB_ENDPOINT_URL` is absent, which is how …" — so a bare substring
    search over the file finds the prose explaining the rule and reports it as a
    violation of it. Structural assertions read this; assertions about the documentation
    read the raw text.
    """
    return "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))


DATA_TF = tf(DATA)
COMPUTE_TF = tf(COMPUTE)
DATA_CODE = code_only(DATA_TF)
COMPUTE_CODE = code_only(COMPUTE_TF)
ALARMS_TF = (COMPUTE / "alarms.tf").read_text(encoding="utf-8")


# --- both roots exist and are wired the way the split claims -------------------------------


def test_the_deployment_is_two_roots_split_by_lifetime() -> None:
    """`destroy compute, keep data` is only a first-class operation if they are separate.

    Asserted rather than described because the whole Phase 10 plan depends on it: the EKS
    window reuses this phase's RDS and DynamoDB, which is impossible if one `terraform
    destroy` takes both.
    """
    assert (DATA / "versions.tf").exists()
    assert (COMPUTE / "versions.tf").exists()
    assert "aws_db_instance" in DATA_TF and "aws_db_instance" not in COMPUTE_TF
    assert "aws_dynamodb_table" in DATA_TF and "aws_dynamodb_table" not in COMPUTE_TF
    assert "aws_ecs_service" in COMPUTE_TF and "aws_ecs_service" not in DATA_TF
    assert "aws_lambda_function" in COMPUTE_TF and "aws_lambda_function" not in DATA_TF


def test_the_data_layer_names_nothing_in_the_compute_layer() -> None:
    """The dependency points one way, and this is what makes the targeted destroy safe.

    A rule in the data layer naming a compute security group is the shape that fails a
    `destroy` with `DependencyViolation` at the worst possible moment — so the data layer
    owns the *group* and compute's members join it.
    """
    for forbidden in ("aws_ecs_", "aws_lb", "aws_lambda_", "aws_cloudwatch_"):
        assert forbidden not in DATA_TF, f"the data layer reaches into compute via {forbidden}"


def test_compute_reads_the_data_layer_through_its_published_outputs() -> None:
    assert 'data "terraform_remote_state" "data"' in COMPUTE_TF
    outputs = (DATA / "outputs.tf").read_text(encoding="utf-8")
    for name in re.findall(r"local\.data\.(\w+)", COMPUTE_TF):
        assert f'output "{name}"' in outputs, (
            f"compute reads an output the data layer never made: {name}"
        )


# --- built to die --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("flag", "why"),
    [
        (
            "skip_final_snapshot     = true",
            "a final snapshot outlives the instance and bills for storage",
        ),
        (
            "deletion_protection     = false",
            "protection on means a destroy fails with a message about a flag",
        ),
        ("backup_retention_period  = 0", "automated backups survive the instance they belonged to"),
        ("delete_automated_backups = true", "and are deleted with it"),
    ],
)
def test_the_database_carries_every_destroy_flag(flag: str, why: str) -> None:
    """§P9: "all the destroy flags from day one". This stack is built to die cleanly."""
    normalised = re.sub(r"[ \t]+", " ", DATA_TF)
    assert re.sub(r"[ \t]+", " ", flag) in normalised, why


def test_the_other_destroy_flags_are_on_too() -> None:
    flat = re.sub(r"[ \t]+", " ", DATA_CODE)
    assert flat.count("force_delete = true") == 2, (
        "both ECR repositories — one holding images refuses to be destroyed without it, "
        "which is every repository that was ever used"
    )
    assert flat.count("deletion_protection_enabled = false") == 2, "both DynamoDB tables"
    assert flat.count("recovery_window_in_days = 0") == 3, (
        "all three secrets: the default 30-day window keeps the *name* reserved, so a "
        "teardown-and-rebuild fails for a month"
    )
    assert "enable_deletion_protection = false" in COMPUTE_CODE, "the load balancer"


def test_log_groups_are_created_here_so_they_are_destroyed_here() -> None:
    """A group ECS or Lambda creates implicitly survives the destroy, keeps `never expire`
    retention, and is exactly what a per-service empty check misses."""
    assert COMPUTE_TF.count('resource "aws_cloudwatch_log_group"') == 3
    assert COMPUTE_TF.count("retention_in_days = var.log_retention_days") == 3


# --- no secret is committed, and none reaches state ------------------------------------------


def test_terraform_creates_secret_containers_and_never_a_version() -> None:
    """Invariant 3, structurally: an `aws_secretsmanager_secret_version` with a
    `secret_string` would put the value in state, in the file that forbids it."""
    assert 'resource "aws_secretsmanager_secret"' in DATA_CODE
    assert "aws_secretsmanager_secret_version" not in DATA_CODE
    assert "aws_secretsmanager_secret_version" not in COMPUTE_CODE
    assert "secret_string" not in DATA_CODE + COMPUTE_CODE


def test_the_database_password_is_managed_by_rds_and_never_by_terraform() -> None:
    assert "manage_master_user_password = true" in DATA_CODE
    assert not re.search(r"^\s*password\s*=", DATA_CODE, re.MULTILINE)
    assert "random_password" not in DATA_CODE, "its `result` is in state too"


@pytest.mark.parametrize(
    "pattern",
    [
        # Shaped like a real credential rather than like the prefix of one: the runbook
        # says `--secret-string "sk-ant-…"` and must go on being able to.
        r"sk-ant-[A-Za-z0-9_-]{16,}",
        r"AKIA[0-9A-Z]{16}",
        r"hk_[A-Za-z0-9_-]{20,}",
    ],
)
def test_no_credential_shaped_string_is_committed_under_deploy(pattern: str) -> None:
    for path in DEPLOY.rglob("*"):
        if path.is_file() and path.suffix in {".tf", ".md", ".example", ".py"}:
            assert not re.search(pattern, path.read_text(encoding="utf-8")), path


def test_the_only_tfvars_git_keeps_is_the_example() -> None:
    """`.gitignore` keeps `*.tfvars.example` and drops the rest — the file the operator's
    home address goes in is never a file git has heard of."""
    gitignore = (REPO / ".gitignore").read_text(encoding="utf-8")
    assert "*.tfvars" in gitignore
    assert "!*.tfvars.example" in gitignore
    assert not list(DEPLOY.rglob("terraform.tfvars"))


def test_the_task_definition_puts_no_secret_in_plain_environment() -> None:
    """`environment` is readable by anyone with `ecs:DescribeTaskDefinition`; `secrets`
    is resolved at task start by the execution role. The three values that matter are in
    the second block, and this asserts they are in neither the first nor the file."""
    ecs = (COMPUTE / "ecs.tf").read_text(encoding="utf-8")
    environment_block = ecs.split("secrets = [")[0]
    for name in ("DATABASE_URL", "HEADROOM_ADMIN_TOKEN", "ANTHROPIC_API_KEY"):
        assert f'name = "{name}"' not in environment_block


# --- the alarms name fields and values the gateway really produces ------------------------------


def alarm_patterns() -> list[str]:
    """Every CloudWatch Logs filter pattern in `alarms.tf`."""
    heredocs = re.findall(r"pattern\s*=\s*<<-EOT\n(.*?)\n\s*EOT", ALARMS_TF, re.DOTALL)
    inline = re.findall(r'pattern\s*=\s*"(.*?)"\n', ALARMS_TF)
    patterns = heredocs + [value.replace('\\"', '"') for value in inline]
    assert patterns, "alarms.tf declares no log filter patterns — did the parsing break?"
    return patterns


#: The `event` key belongs to `headroom/metering/writer.py`'s warnings rather than to the
#: per-request line, and is the only field these filters name that a `RequestContext`
#: does not carry.
WRITER_FIELDS = {"event"}


def test_every_field_an_alarm_filters_on_is_a_field_the_gateway_logs() -> None:
    """The silent failure this whole file exists for: rename a log field and the alarm
    stops firing, with nothing red anywhere and no error in any log."""
    logged = set(RequestContext().as_log_fields()) | WRITER_FIELDS

    for pattern in alarm_patterns():
        for field in re.findall(r"\$\.(\w+)", pattern):
            assert field in logged, f"no log line carries {field!r}"


def test_every_value_an_alarm_matches_is_a_value_the_gateway_can_produce() -> None:
    """The other half. A filter on `outcome = "upstream_down"` would be well-formed,
    apply cleanly, and match nothing forever."""
    from headroom.core.errors import ProviderTimeout, ProviderUnavailable
    from headroom.metering.writer import _warn  # noqa: F401 - imported for the module
    from headroom.policy.failover import ATTEMPT_BREAKER_OPEN

    writer_source = (REPO / "headroom" / "metering" / "writer.py").read_text(encoding="utf-8")
    producible = {
        ProviderTimeout.reason,
        ProviderUnavailable.reason,
        ATTEMPT_BREAKER_OPEN,
        # `budget_status` is set by the budget gate; `exceeded` is one of its three values.
        "exceeded",
        # A prefix match, because the status is part of the string: `upstream_status_529`.
        "upstream_status_5*",
        *re.findall(r'_warn\(\s*"(\w+)"', writer_source),
    }

    matched = {value for pattern in alarm_patterns() for value in re.findall(r'"([^"]+)"', pattern)}
    unknown = matched - producible
    assert not unknown, f"an alarm matches values the gateway never emits: {sorted(unknown)}"


def test_an_upstream_client_error_is_deliberately_not_a_provider_failure() -> None:
    """H-052's rule, inherited rather than re-decided: a 400 is a healthy provider
    correctly refusing a bad request, and counting it would let one tenant's malformed
    payloads page an operator about somebody else's outage."""
    [provider_pattern] = [pattern for pattern in alarm_patterns() if "upstream_timeout" in pattern]
    assert "upstream_status_4" not in provider_pattern
    assert "upstream_error" not in provider_pattern


def test_the_alarms_have_somewhere_to_page() -> None:
    """An alarm with no action changes colour in a console nobody is looking at."""
    for alarm in re.findall(r'resource "aws_cloudwatch_metric_alarm" "(\w+)"', ALARMS_TF):
        assert alarm  # named, so the loop below reads as a per-alarm assertion
    assert ALARMS_TF.count("alarm_actions = local.alarm_actions") == 4
    assert ALARMS_TF.count("ok_actions    = local.alarm_actions") == 4, (
        "and somewhere to say it recovered — 'did it clear' is the second question "
        "every page produces"
    )


# --- the tables, the endpoint, and the handler ----------------------------------------------


def test_the_dynamodb_tables_are_the_names_the_gateway_defaults_to() -> None:
    assert f'name         = "{DEFAULT_BUDGETS_TABLE}"' in DATA_TF
    assert f'name         = "{DEFAULT_BUCKETS_TABLE}"' in DATA_TF
    # Passed explicitly to the task as well: a deployment should not rest on a default
    # staying put, and this is the pin that makes the two agree.
    assert "value = local.data.budgets_table_name" in COMPUTE_TF
    assert "value = local.data.buckets_table_name" in COMPUTE_TF


def test_the_bucket_table_reaps_the_attribute_the_limiter_actually_writes() -> None:
    """H-035 said enabling TTL was Terraform's job in Phase 9. This is that, and it is on
    the attribute `headroom/db/buckets.py` sets on every consumption."""
    buckets_source = (REPO / "headroom" / "db" / "buckets.py").read_text(encoding="utf-8")
    assert "expires_at = :ttl" in buckets_source
    assert 'attribute_name = "expires_at"' in DATA_TF
    assert "enabled        = true" in DATA_TF
    # And not on the budgets table — H-032 rejects TTL there by name, because deleting a
    # budget item because a reservation inside it expired strands the tenant's cap.
    budgets_block = DATA_TF.split('resource "aws_dynamodb_table" "buckets"')[0]
    assert "ttl {" not in budgets_block


def test_the_deployed_gateway_is_not_told_about_an_emulator() -> None:
    """The one line that is *absent*, and the whole of assumption A1's second half: with
    no endpoint override, `headroom/db/dynamo.py` resolves the regional endpoint and signs
    with the task role instead of the emulator's dummy credential."""
    assert DYNAMODB_ENDPOINT_ENV not in COMPUTE_CODE
    assert DYNAMODB_ENDPOINT_ENV not in DATA_CODE


def test_the_rollup_lambda_is_given_the_secret_arn_the_handler_reads() -> None:
    """Without this, `database_url()` falls through to the compose default — in a VPC
    where nothing is listening on it, once a night, silently."""
    lambda_tf = (COMPUTE / "lambda.tf").read_text(encoding="utf-8")
    assert f"{DATABASE_URL_SECRET_ARN_ENV} = local.data.database_url_secret_arn" in lambda_tf


def test_the_lambda_handler_string_names_a_function_that_exists() -> None:
    lambda_tf = (COMPUTE / "lambda.tf").read_text(encoding="utf-8")
    [declared] = re.findall(r'handler\s*=\s*"([\w.]+)"', lambda_tf)

    module_name, _, function_name = declared.rpartition(".")
    module = __import__(module_name, fromlist=[function_name])
    assert callable(getattr(module, function_name))


def test_the_lambda_ships_the_package_it_imports() -> None:
    """The build script copies `headroom` and installs the lockfile's asyncpg — nothing
    else. If the handler grows an import of `fastapi`, this is where it is noticed."""
    build = (DEPLOY / "lambda" / "build.py").read_text(encoding="utf-8")
    assert 'VENDORED = ("asyncpg",)' in build
    assert 'shutil.copytree(REPO / "headroom"' in build


# --- the runbook and the configuration agree ---------------------------------------------------


def test_the_runbook_fills_in_every_secret_terraform_creates() -> None:
    """A secret with no version makes the task fail to start. The runbook is what stops
    that being discovered at apply time."""
    secrets_tf = code_only((DATA / "secrets.tf").read_text(encoding="utf-8"))
    names = re.findall(r'\w+\s*=\s*"\$\{var\.project\}/([\w-]+)"', secrets_tf)
    assert len(names) == 3, f"expected three secrets, parsed {names}"
    for name in names:
        assert f"--secret-id headroom/{name}" in RUNBOOK, f"the runbook never sets {name}"


def test_the_runbook_uses_the_migration_runner_the_repo_ships() -> None:
    """§P9: "migrations run by the same runner as everywhere (same code local and prod)".

    The module path rather than a whole command line: on AWS it arrives as a JSON
    `containerOverrides` array, so the string that has to stay true is the import path.
    """
    assert "headroom.db.migrate" in RUNBOOK
    assert (REPO / "headroom" / "db" / "migrate.py").exists()
    # And every migration on disk is one the runbook says it expects to see applied.
    for path in sorted((REPO / "migrations").glob("*.sql")):
        assert path.stem in RUNBOOK, f"{path.stem} is not in the runbook's expected output"


def test_every_required_variable_appears_in_the_tfvars_template() -> None:
    """A variable with no default stops `terraform plan` with a prompt. The template is
    what makes that a copy-and-edit rather than a hunt."""
    for root in (DATA, COMPUTE):
        source = tf(root)
        example = (root / "terraform.tfvars.example").read_text(encoding="utf-8")
        for block in re.findall(r'variable "(\w+)" \{(.*?)\n\}', source, re.DOTALL):
            name, body = block
            if "default" not in body:
                assert re.search(rf"^{name}\s*=", example, re.MULTILINE), (
                    f"{root.name}: {name} has no default and no line in the tfvars template"
                )


def test_the_only_source_address_the_alb_admits_has_no_default() -> None:
    """A default of `0.0.0.0/0` publishes a tenant-and-key control plane the first time
    somebody forgets a flag; a default of somebody's old address fails closed in a way
    that reads like a networking problem. Terraform refusing to plan is correct."""
    variables = (COMPUTE / "variables.tf").read_text(encoding="utf-8")
    [block] = re.findall(r'variable "home_cidr" \{(.*?)\n\}\n', variables, re.DOTALL)
    # An assignment, not the word — the description argues at length about defaults.
    assert not re.search(r"^\s*default\s*=", block, re.MULTILINE)
    assert 'var.home_cidr != "0.0.0.0/0"' in variables


# --- the tags the billing lesson is about --------------------------------------------------------

REQUIRED_TAGS = ("Project", "Layer", "Phase", "ManagedBy")


@pytest.mark.parametrize("root", ["data", "compute"])
def test_every_resource_carries_the_cost_allocation_tags(root: str) -> None:
    """`default_tags` on the provider rather than a `tags` block per resource: a tag
    added to eleven resources by hand is a tag missing from the twelfth, and §0.4's A7
    names Backline's cost chase as the reason this matters."""
    versions = (DEPLOY / root / "versions.tf").read_text(encoding="utf-8")
    assert "default_tags" in versions
    for tag in REQUIRED_TAGS:
        assert re.search(rf"^\s*{tag}\s*=", versions, re.MULTILINE), f"{root} does not tag {tag}"
    assert f'Layer     = "{root}"' in versions


def test_the_runbook_activates_the_tags_before_anything_is_charged_by_the_hour() -> None:
    """The lesson §P9 names by hand, and the chicken-and-egg it does not: AWS will not
    offer a tag key for activation until it has seen it on a resource. So the runbook
    creates the two free ECR repositories first, activates, then applies the rest."""
    assert "update-cost-allocation-tags-status" in RUNBOOK
    assert "-target=aws_ecr_repository" in RUNBOOK
    for tag in REQUIRED_TAGS:
        assert tag in RUNBOOK


def test_the_runbook_states_a_cost_before_every_paid_step() -> None:
    """§0.6's discipline: "state the expected daily cost of the deployed stack before the
    first apply". Not a vibe — the table has to name the four lines that dominate it."""
    for line in ("ALB", "RDS", "Fargate", "endpoint"):
        assert line in RUNBOOK
    assert "$" in RUNBOOK and "per day" in RUNBOOK
