{{/*
Names and labels.

Kubernetes object names are DNS-1123 (lowercase alphanumerics and `-`, 63 characters),
and a *label value* is 63 characters with no exception at all — so a release name long
enough to overflow one produces an object that fails to apply with a message about
`metadata.labels`, several layers from the thing that caused it. Every name below is
truncated to 63 and to 55 where a component suffix is appended, and
`tests/test_deploy_k8s.py` renders the chart under a deliberately long release name to
prove it.
*/}}

{{- define "headroom.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "headroom.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 55 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 55 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 55 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "headroom.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* Labels that identify the release. On every object. */}}
{{- define "headroom.labels" -}}
helm.sh/chart: {{ include "headroom.chart" . }}
{{ include "headroom.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: headroom
{{- with .Values.commonLabels }}
{{ toYaml . }}
{{- end }}
{{- end -}}

{{- define "headroom.selectorLabels" -}}
app.kubernetes.io/name: {{ include "headroom.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/*
Per-component selector labels.

A selector is immutable on a Deployment, so these three lines are the one part of this
chart that cannot be changed without deleting and recreating the object. They are
deliberately minimal for that reason: name, instance, component, and nothing that moves.
Version and chart labels stay out — bumping the chart must not orphan a ReplicaSet.
*/}}
{{- define "headroom.componentSelectorLabels" -}}
{{ include "headroom.selectorLabels" .root }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "headroom.componentLabels" -}}
{{ include "headroom.labels" .root }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{/*
The gateway's environment.

**This block is the compose-parity claim**, and it is the reason the chart is worth
reading beside `docker-compose.yml` and `deploy/aws/compute/ecs.tf`. Every name below is
a name those two files set, spelled identically, read by the same code. What differs
between the three environments is where a value comes from, never what the gateway is
looking for:

  compose  DATABASE_URL from the compose network      DYNAMODB_ENDPOINT_URL set
  ECS      DATABASE_URL from Secrets Manager           DYNAMODB_ENDPOINT_URL absent
  EKS      DATABASE_URL from a Kubernetes Secret       DYNAMODB_ENDPOINT_URL absent

`tests/test_deploy_k8s.py` parses the ECS task definition and asserts every variable it
sets appears here, so the parity is checked rather than claimed — and asserts
`DYNAMODB_ENDPOINT_URL` appears nowhere under `deploy/k8s/`, which is assumption A1's one
line of difference, now on a third runtime.
*/}}
{{- define "headroom.gatewayEnv" -}}
{{- /* boto3 needs a region, and a pod's metadata service is not a region source it reads. */ -}}
- name: AWS_REGION
  value: {{ .Values.aws.region | quote }}
{{- /* Fargate injects AWS_DEFAULT_REGION for free and this botocore reads only that
   name for env region resolution; Kubernetes injects nothing, so we set both. Found
   at first cluster smoke: NoRegionError with AWS_REGION present. */}}
- name: AWS_DEFAULT_REGION
  value: {{ .Values.aws.region | quote }}
- name: HEADROOM_BUDGETS_TABLE
  value: {{ .Values.aws.budgetsTable | quote }}
- name: HEADROOM_BUCKETS_TABLE
  value: {{ .Values.aws.bucketsTable | quote }}
- name: HEADROOM_LOG_LEVEL
  value: {{ .Values.gateway.logLevel | quote }}
{{- /* The other half of the preStop hook: the hook writes this path, the gateway watches
   it and starts answering `Connection: close`. One value, two consumers, so they cannot
   disagree about where the sentinel lives (H-091). */}}
- name: HEADROOM_DRAIN_FILE
  value: {{ .Values.gateway.lifecycle.drainFilePath | quote }}
- name: HF_HOME
  value: {{ .Values.gateway.embed.hfHome | quote }}
{{- if .Values.gateway.embed.offline }}
- name: HF_HUB_OFFLINE
  value: "1"
{{- end }}
{{- if .Values.vllm.enabled }}
{{- /*
  The two vLLM instances, reached through the tailscale egress proxy. Same variable names
  compose sets, pointed at a Service instead of at `host.docker.internal` — which is what
  makes the P6/P7 kill demo runnable against a cluster gateway with no change to
  `config/routing.yaml`.
*/}}
- name: VLLM_BASE_URL
  value: http://{{ include "headroom.fullname" . }}-vllm:{{ .Values.vllm.ports.a }}
- name: VLLM_B_BASE_URL
  value: http://{{ include "headroom.fullname" . }}-vllm:{{ .Values.vllm.ports.b }}
{{- end }}
{{- with .Values.gateway.extraEnv }}
{{ toYaml . }}
{{- end }}
{{- /*
  The three credentials, by reference. `valueFrom.secretKeyRef` is the Kubernetes
  equivalent of the ECS task definition's `secrets` block and it has the same property:
  the value is never in the object a `kubectl get deploy -o yaml` prints, never in a
  values file, and never in this repo (BUILD_PLAN §0.2 invariant 3).
*/}}
- name: DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.existingSecret }}
      key: {{ .Values.secrets.keys.databaseUrl }}
- name: HEADROOM_ADMIN_TOKEN
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.existingSecret }}
      key: {{ .Values.secrets.keys.adminToken }}
- name: ANTHROPIC_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.existingSecret }}
      key: {{ .Values.secrets.keys.anthropicApiKey }}
{{- end -}}

{{/*
A LoadBalancer with no source ranges is refused at render time.

`deploy/aws/compute/variables.tf` gives `home_cidr` no default and rejects `0.0.0.0/0`,
and argues why at length: a default of "everywhere" publishes a tenant-and-key control
plane the first time somebody forgets a flag, and a default of somebody's old address
fails closed in a way that reads like a networking problem. Terraform refusing to plan is
the correct behaviour, and this is the same refusal one runtime over.

It fires at `helm template` time, so it is caught by the runbook's own dry run rather than
by a load balancer that has already been created.
*/}}
{{- define "headroom.requireSourceRanges" -}}
{{- if and (eq .service.type "LoadBalancer") (not .service.loadBalancerSourceRanges) -}}
{{- fail (printf "%s.service.type is LoadBalancer with no loadBalancerSourceRanges: refusing to publish the %s to 0.0.0.0/0. Set %s.service.loadBalancerSourceRanges (deploy/k8s/README.md section 4)" .component .component .component) -}}
{{- end -}}
{{- end -}}
