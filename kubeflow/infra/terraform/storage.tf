resource "kubernetes_storage_class" "local_cache" {
  metadata {
    name = local.cache_storage_class_name
    labels = {
      "labclip.io/iac-stack" = local.iac_stack_name
    }
  }

  storage_provisioner    = "kubernetes.io/no-provisioner"
  volume_binding_mode    = "WaitForFirstConsumer"
  reclaim_policy         = "Retain"
  allow_volume_expansion = false

  depends_on = [terraform_data.ownership_guard]
}

resource "kubernetes_persistent_volume" "cache" {
  for_each = var.nodes

  metadata {
    name = each.value.cache_claim
    labels = {
      "labclip.io/cache-profile" = each.key
    }
  }

  spec {
    capacity = {
      storage = "${each.value.cache_capacity_gi}Gi"
    }
    access_modes                     = ["ReadWriteOnce"]
    storage_class_name               = kubernetes_storage_class.local_cache.metadata[0].name
    persistent_volume_reclaim_policy = "Retain"
    volume_mode                      = "Filesystem"

    persistent_volume_source {
      local {
        path = each.value.cache_root
      }
    }

    node_affinity {
      required {
        node_selector_term {
          match_expressions {
            key      = "kubernetes.io/hostname"
            operator = "In"
            values   = [each.key]
          }
        }
      }
    }
  }
}

locals {
  cache_claims          = local.kubeflow_run_bindings_enabled ? var.nodes : {}
  cache_claim_namespace = var.labclip_run_namespace
}

resource "kubernetes_persistent_volume_claim" "cache" {
  for_each = local.cache_claims

  wait_until_bound = false

  metadata {
    name      = each.value.cache_claim
    namespace = local.cache_claim_namespace
  }

  spec {
    access_modes       = ["ReadWriteOnce"]
    storage_class_name = kubernetes_storage_class.local_cache.metadata[0].name

    selector {
      match_labels = {
        "labclip.io/cache-profile" = each.key
      }
    }

    resources {
      requests = {
        storage = "${each.value.cache_claim_request_gi}Gi"
      }
    }
  }

  depends_on = [terraform_data.kubeflow_run_bindings_guard]
}
