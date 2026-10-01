data "kubernetes_resources" "ownership_namespace" {
  api_version    = "v1"
  kind           = "Namespace"
  field_selector = "metadata.name=${var.argo_namespace}"
}

data "kubernetes_resources" "ownership_storage_class" {
  api_version    = "storage.k8s.io/v1"
  kind           = "StorageClass"
  field_selector = "metadata.name=${local.cache_storage_class_name}"
}

locals {
  iac_stack_name           = "argo"
  cache_storage_class_name = "labclip-local-cache"
  foreign_stack_namespaces = [
    for ns in data.kubernetes_resources.ownership_namespace.objects : ns.metadata.name
    if try(ns.metadata.labels["labclip.io/iac-stack"], local.iac_stack_name) != local.iac_stack_name
  ]
  foreign_stack_storage_classes = [
    for sc in data.kubernetes_resources.ownership_storage_class.objects : sc.metadata.name
    if try(sc.metadata.labels["labclip.io/iac-stack"], local.iac_stack_name) != local.iac_stack_name
  ]
}

resource "terraform_data" "ownership_guard" {
  input = local.iac_stack_name

  lifecycle {
    precondition {
      condition     = length(local.foreign_stack_namespaces) + length(local.foreign_stack_storage_classes) == 0
      error_message = "The ${var.argo_namespace} namespace or the ${local.cache_storage_class_name} StorageClass belongs to another LabCLIP IaC stack. Destroy that stack before applying this one."
    }
  }
}
