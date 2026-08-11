# Everything this root knows about the data layer, in one place.
#
# `terraform_remote_state` rather than data sources that look resources up by name or
# tag. Both work; the difference is what happens when the data layer has not been applied
# yet. A name lookup fails with "no matching VPC found", which reads like a networking
# problem; this fails with "no state file at ../data/terraform.tfstate", which reads like
# what it is. The runbook's order — data, then compute — is enforced by the error message.
#
# It also means the coupling is exactly the data layer's `outputs.tf` and nothing else:
# no compute resource can reach for an attribute the data layer did not choose to
# publish, which is the same argument H-054 makes about the console reading `/admin/*`
# rather than the database.

data "terraform_remote_state" "data" {
  backend = "local"

  config = {
    path = var.data_state_path
  }
}

data "aws_caller_identity" "current" {}

locals {
  data = data.terraform_remote_state.data.outputs

  # The console reaches the gateway by its service-discovery name, not through the load
  # balancer. Two reasons, and the first is the one that matters: the ALB's security group
  # admits exactly one source address (the operator's /32), and it should stay that way —
  # adding "and also anything in this VPC" to make one service talk to another would
  # weaken the control §P9 asks for by name, to solve a problem service discovery solves
  # for two cents a day. The second is that a request from the console to the gateway has
  # no business leaving the VPC and coming back through a public load balancer.
  gateway_internal_url = "http://gateway.${aws_service_discovery_private_dns_namespace.main.name}:8000"
}
