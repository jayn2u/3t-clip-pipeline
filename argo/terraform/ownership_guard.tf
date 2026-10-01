data "kubernetes_resources" "ownership_namespace" {
  api_version    = "v1"
  kind           = "Namespace"
  field_selector = "metadata.name=${var.argo_namespace}"
}

locals {
  iac_stack_name = "argo"
  foreign_stack_namespaces = [
    for ns in data.kubernetes_resources.ownership_namespace.objects : ns.metadata.name
    if try(ns.metadata.labels["labclip.io/iac-stack"], local.iac_stack_name) != local.iac_stack_name
  ]
}

resource "terraform_data" "ownership_guard" {
  input = local.iac_stack_name

  lifecycle {
    precondition {
      condition     = length(local.foreign_stack_namespaces) == 0
      error_message = "The ${var.argo_namespace} namespace belongs to another LabCLIP IaC stack. Destroy that stack before applying this one."
    }
  }
}
