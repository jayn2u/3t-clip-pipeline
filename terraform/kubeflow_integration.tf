locals {
  kubeflow_run_bindings_enabled = var.platform_mode == "kubeflow" && var.enable_kubeflow_run_bindings
}

resource "kubernetes_secret" "kubeflow_minio_credentials" {
  for_each = local.kubeflow_run_bindings_enabled ? var.nodes : {}

  metadata {
    name      = kubernetes_secret.minio_credentials[each.key].metadata[0].name
    namespace = var.labclip_run_namespace
  }

  data = {
    "access-key" = var.minio_credentials[each.value.minio_store].access_key
    "secret-key" = var.minio_credentials[each.value.minio_store].secret_key
    "endpoint"   = "http://${each.value.minio_role}.${var.argo_namespace}.svc.cluster.local:9000"
    "region"     = "garage"
  }
}

resource "kubernetes_secret" "kubeflow_ghcr" {
  count = local.kubeflow_run_bindings_enabled ? 1 : 0

  metadata {
    name      = kubernetes_secret.ghcr["ghcr-secret"].metadata[0].name
    namespace = var.labclip_run_namespace
  }

  type = kubernetes_secret.ghcr["ghcr-secret"].type
  data = kubernetes_secret.ghcr["ghcr-secret"].data
}

resource "kubernetes_secret" "kubeflow_wandb" {
  count = local.kubeflow_run_bindings_enabled ? 1 : 0

  metadata {
    name      = kubernetes_secret.wandb.metadata[0].name
    namespace = var.labclip_run_namespace
  }

  data = kubernetes_secret.wandb.data
}
