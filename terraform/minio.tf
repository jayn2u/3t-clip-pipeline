# One single-replica MinIO Deployment per node, pinned to that node via
# nodeSelector and backed by the hostPath directory Ansible's local_storage
# role already created. This mirrors the previous pipeline/k8s/minio-*.yaml
# manifests, minus manual secret creation (now a Terraform-managed Secret).

resource "kubernetes_secret" "minio_credentials" {
  for_each = var.nodes

  metadata {
    name      = "${each.value.minio_role}-credentials"
    namespace = kubernetes_namespace.minio.metadata[0].name
  }

  data = {
    MINIO_ROOT_USER     = var.minio_root_user
    MINIO_ROOT_PASSWORD = var.minio_root_password
  }
}

resource "kubernetes_deployment" "minio" {
  for_each = var.nodes

  metadata {
    name      = each.value.minio_role
    namespace = kubernetes_namespace.minio.metadata[0].name
    labels    = { app = each.value.minio_role }
  }

  spec {
    replicas = 1 # local hostPath storage - never scale beyond 1 without moving to real distributed MinIO

    selector {
      match_labels = { app = each.value.minio_role }
    }

    strategy {
      type = "Recreate" # avoid two pods writing the same hostPath concurrently during rollout
    }

    template {
      metadata {
        labels = { app = each.value.minio_role }
      }

      spec {
        node_selector = {
          "kubernetes.io/hostname" = each.key
        }

        container {
          name  = "minio"
          image = "minio/minio:RELEASE.2024-08-17T01-24-54Z"
          args  = ["server", "/data", "--console-address", ":9001"]

          env_from {
            secret_ref {
              name = kubernetes_secret.minio_credentials[each.key].metadata[0].name
            }
          }

          port {
            container_port = 9000
            name           = "api"
          }
          port {
            container_port = 9001
            name           = "console"
          }

          volume_mount {
            name       = "data"
            mount_path = "/data"
          }

          readiness_probe {
            http_get {
              path = "/minio/health/ready"
              port = 9000
            }
            initial_delay_seconds = 5
          }
        }

        volume {
          name = "data"
          host_path {
            path = each.value.minio_data_root
            type = "DirectoryOrCreate"
          }
        }
      }
    }
  }
}

resource "kubernetes_service" "minio" {
  for_each = var.nodes

  metadata {
    name      = each.value.minio_role
    namespace = kubernetes_namespace.minio.metadata[0].name
  }

  spec {
    selector = { app = each.value.minio_role }

    port {
      name        = "api"
      port        = 9000
      target_port = 9000
    }
    port {
      name        = "console"
      port        = 9001
      target_port = 9001
    }
  }
}
