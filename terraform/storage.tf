# One node-affined local PersistentVolume per node, sized from var.nodes.
# Directory creation/ownership is Ansible's job (roles/local_storage); this
# resource only registers the already-existing directory with Kubernetes.

resource "kubernetes_storage_class" "local_cache" {
  metadata {
    name = "local-cache"
  }
  storage_provisioner    = "kubernetes.io/no-provisioner"
  volume_binding_mode    = "WaitForFirstConsumer"
  reclaim_policy         = "Retain"
}

resource "kubernetes_persistent_volume" "cache" {
  for_each = var.nodes

  metadata {
    name = "cache-${each.key}"
  }

  spec {
    capacity = {
      storage = "${each.value.cache_capacity_gi}Gi"
    }
    access_modes                    = ["ReadWriteOnce"]
    storage_class_name              = kubernetes_storage_class.local_cache.metadata[0].name
    persistent_volume_reclaim_policy = "Retain"

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
