locals {
  minio_image = "ghcr.io/jayn2u/minio-community:RELEASE.2025-10-15T17-29-55Z@sha256:da49a0b1871cde11cafeb09096ecfbf4704cbdafbf0095e683031db9194e05ca"
}

resource "kubernetes_secret" "minio_credentials" {
  for_each = var.nodes

  metadata {
    name      = "${each.value.minio_role}-secret"
    namespace = kubernetes_namespace.argo.metadata[0].name
  }

  data = merge(
    {
      "root-user"     = var.minio_root_user
      "root-password" = var.minio_root_password
      "access-key"    = var.minio_credentials[each.value.minio_store].access_key
      "secret-key"    = var.minio_credentials[each.value.minio_store].secret_key
      "endpoint"      = "http://${each.value.minio_role}.${var.argo_namespace}.svc.cluster.local:9000"
      "region"        = "garage"
    },
    each.value.minio_external_endpoint == "" ? {} : {
      "endpoint-external" = each.value.minio_external_endpoint
    }
  )
}

resource "kubernetes_secret" "minio_researcher_credentials" {
  for_each = var.nodes

  metadata {
    name      = "${each.value.minio_role}-researcher-secret"
    namespace = kubernetes_namespace.argo.metadata[0].name
  }

  data = {
    "access-key" = var.minio_credentials[each.value.minio_store].researcher_access_key
    "secret-key" = var.minio_credentials[each.value.minio_store].researcher_secret_key
  }
}

resource "kubernetes_secret" "ghcr" {
  for_each = toset(["ghcr-secret", "ghcr-pull-secret"])

  metadata {
    name      = each.key
    namespace = kubernetes_namespace.argo.metadata[0].name
  }

  type = "kubernetes.io/dockerconfigjson"
  data = {
    ".dockerconfigjson" = var.ghcr_dockerconfigjson
  }
}

resource "kubernetes_secret" "wandb" {
  metadata {
    name      = "wandb-secret"
    namespace = kubernetes_namespace.argo.metadata[0].name
  }

  data = {
    "api-key" = var.wandb_api_key
    "entity"  = var.wandb_entity
    "project" = var.wandb_project
  }
}

resource "kubernetes_deployment" "minio" {
  for_each = var.nodes

  metadata {
    name      = each.value.minio_role
    namespace = kubernetes_namespace.argo.metadata[0].name
    labels    = { app = each.value.minio_role }
  }

  spec {
    replicas = 1

    selector {
      match_labels = { app = each.value.minio_role }
    }

    strategy {
      type = "Recreate"
    }

    template {
      metadata {
        labels = { app = each.value.minio_role }
        annotations = {
          "labclip.io/minio-root-credentials-sha256" = sha256(jsonencode({
            user     = var.minio_root_user
            password = var.minio_root_password
          }))
        }
      }

      spec {
        image_pull_secrets {
          name = kubernetes_secret.ghcr["ghcr-pull-secret"].metadata[0].name
        }

        node_selector = {
          "kubernetes.io/hostname" = each.key
        }

        toleration {
          key      = "node-role.kubernetes.io/control-plane"
          operator = "Exists"
          effect   = "NoSchedule"
        }

        toleration {
          key      = "node-role.kubernetes.io/master"
          operator = "Exists"
          effect   = "NoSchedule"
        }

        container {
          name              = "minio"
          image             = local.minio_image
          image_pull_policy = "IfNotPresent"
          args              = ["server", "/data", "--address", ":9000", "--console-address", ":9001"]

          env {
            name = "MINIO_ROOT_USER"
            value_from {
              secret_key_ref {
                name = kubernetes_secret.minio_credentials[each.key].metadata[0].name
                key  = "root-user"
              }
            }
          }

          env {
            name = "MINIO_ROOT_PASSWORD"
            value_from {
              secret_key_ref {
                name = kubernetes_secret.minio_credentials[each.key].metadata[0].name
                key  = "root-password"
              }
            }
          }

          env {
            name  = "MINIO_REGION_NAME"
            value = "garage"
          }

          env {
            name  = "MINIO_BROWSER"
            value = "on"
          }

          port {
            container_port = 9000
            name           = "api"
            host_port      = each.value.minio_host_port
            host_ip        = each.value.minio_host_port == null ? null : "127.0.0.1"
          }

          port {
            container_port = 9001
            name           = "console"
          }

          volume_mount {
            name       = "data"
            mount_path = "/data"
          }

          resources {
            requests = each.value.minio_store == "code" ? {
              cpu    = "100m"
              memory = "256Mi"
              } : {
              cpu    = "250m"
              memory = "512Mi"
            }
            limits = each.value.minio_store == "code" ? {
              cpu    = "1"
              memory = "1Gi"
              } : {
              cpu    = "2"
              memory = "4Gi"
            }
          }

          readiness_probe {
            http_get {
              path = "/minio/health/ready"
              port = 9000
            }
            period_seconds = 10
          }

          liveness_probe {
            http_get {
              path = "/minio/health/live"
              port = 9000
            }
            initial_delay_seconds = 15
            period_seconds        = 20
          }
        }

        volume {
          name = "data"
          host_path {
            path = each.value.minio_data_root
            type = "Directory"
          }
        }
      }
    }
  }

  lifecycle {
    precondition {
      condition = nonsensitive(
        var.minio_root_user != var.minio_credentials[each.value.minio_store].access_key &&
        var.minio_root_password != var.minio_credentials[each.value.minio_store].secret_key
      )
      error_message = "MinIO root credentials must differ from the pipeline user credentials."
    }
  }

  depends_on = [
    kubernetes_secret.ghcr,
    kubernetes_secret.minio_credentials,
  ]
}

resource "kubernetes_service" "minio" {
  for_each = var.nodes

  metadata {
    name      = each.value.minio_role
    namespace = kubernetes_namespace.argo.metadata[0].name
  }

  spec {
    type     = "ClusterIP"
    selector = { app = each.value.minio_role }

    port {
      name        = "api"
      port        = 9000
      target_port = "api"
    }

    port {
      name        = "console"
      port        = 9001
      target_port = "console"
    }
  }

}

resource "terraform_data" "minio_bootstrap" {
  triggers_replace = [
    "labclip-minio-bootstrap-v1",
    local.minio_image,
    sha256(jsonencode({
      root_user     = var.minio_root_user
      root_password = var.minio_root_password
      credentials   = var.minio_credentials
    })),
    sha256(jsonencode({
      for name, node in var.nodes : name => {
        role              = node.minio_role
        store             = node.minio_store
        data_root         = node.minio_data_root
        external_endpoint = node.minio_external_endpoint
      }
    })),
  ]

  provisioner "local-exec" {
    command     = "bash \"${path.module}/../scripts/bootstrap_minio.sh\""
    working_dir = path.module
    environment = {
      KUBECONFIG = abspath(var.kubeconfig_path)
    }
  }

  depends_on = [
    kubernetes_deployment.minio,
    kubernetes_secret.minio_credentials,
    kubernetes_secret.minio_researcher_credentials,
    kubernetes_service.minio,
  ]
}
