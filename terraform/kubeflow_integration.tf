locals {
  kubeflow_run_bindings_enabled = var.platform_mode == "kubeflow" && var.enable_kubeflow_run_bindings
}

data "kubernetes_resource" "kubeflow_cache" {
  for_each = local.kubeflow_run_bindings_enabled ? var.nodes : {}

  api_version = "v1"
  kind        = "PersistentVolume"

  metadata {
    name = each.value.cache_claim
  }
}

resource "terraform_data" "kubeflow_run_bindings_guard" {
  count = local.kubeflow_run_bindings_enabled ? 1 : 0

  input = {
    run_namespace                = var.labclip_run_namespace
    cache_pv_rebind_confirmation = var.confirm_kubeflow_cache_pv_rebind
  }

  lifecycle {
    precondition {
      condition     = var.labclip_run_namespace != var.argo_namespace
      error_message = "Kubeflow run bindings require labclip_run_namespace to differ from argo_namespace because the source Secrets already exist in the Argo namespace."
    }

    precondition {
      condition     = var.confirm_kubeflow_cache_pv_rebind
      error_message = "Retained cache PVs can remain Released with a stale claimRef after the Argo PVC is removed. Verify that the original PVC is absent and each PV's phase, claimRef, node affinity, local path, and retained data are correct; clear a stale claimRef only after those checks, verify the PV is Available, then explicitly set confirm_kubeflow_cache_pv_rebind=true."
    }

    precondition {
      condition = alltrue([
        for node_name, node in var.nodes : try(
          (
            data.kubernetes_resource.kubeflow_cache[node_name].object["status"]["phase"] == "Available" &&
            try(length(data.kubernetes_resource.kubeflow_cache[node_name].object["spec"]["claimRef"]), 0) == 0
            ) || (
            data.kubernetes_resource.kubeflow_cache[node_name].object["status"]["phase"] == "Bound" &&
            data.kubernetes_resource.kubeflow_cache[node_name].object["spec"]["claimRef"]["namespace"] == var.labclip_run_namespace &&
            data.kubernetes_resource.kubeflow_cache[node_name].object["spec"]["claimRef"]["name"] == node.cache_claim &&
            try(length(data.kubernetes_resource.kubeflow_cache[node_name].object["spec"]["claimRef"]["uid"]), 0) > 0
          ),
          false
        )
      ])
      error_message = "The cache PV preflight permits only unclaimed Available PVs or PVs Bound to the configured Kubeflow PVC; Released PVs and stale claimRefs must be reconciled manually before enabling run bindings."
    }

    precondition {
      condition = alltrue([
        for node_name, node in var.nodes : try(
          data.kubernetes_resource.kubeflow_cache[node_name].object["spec"]["local"]["path"] == node.cache_root,
          false
        )
      ])
      error_message = "Each Kubeflow cache PV must retain the configured node-local data path."
    }

    precondition {
      condition = alltrue([
        for node_name, node in var.nodes : try(
          length(flatten([
            for term in data.kubernetes_resource.kubeflow_cache[node_name].object["spec"]["nodeAffinity"]["required"]["nodeSelectorTerms"] : [
              for expression in try(term["matchExpressions"], []) : expression
              if try(expression["key"], "") == "kubernetes.io/hostname" &&
              try(expression["operator"], "") == "In" &&
              contains(try(expression["values"], []), node_name)
            ]
          ])) == 1,
          false
        )
      ])
      error_message = "Each Kubeflow cache PV must retain node affinity to its configured LabCLIP node."
    }
  }
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

  depends_on = [terraform_data.kubeflow_run_bindings_guard]
}

resource "kubernetes_secret" "kubeflow_ghcr" {
  count = local.kubeflow_run_bindings_enabled ? 1 : 0

  metadata {
    name      = kubernetes_secret.ghcr["ghcr-secret"].metadata[0].name
    namespace = var.labclip_run_namespace
  }

  type = kubernetes_secret.ghcr["ghcr-secret"].type
  data = kubernetes_secret.ghcr["ghcr-secret"].data

  depends_on = [terraform_data.kubeflow_run_bindings_guard]
}

resource "kubernetes_secret" "kubeflow_wandb" {
  count = local.kubeflow_run_bindings_enabled ? 1 : 0

  metadata {
    name      = kubernetes_secret.wandb.metadata[0].name
    namespace = var.labclip_run_namespace
  }

  data = kubernetes_secret.wandb.data

  depends_on = [terraform_data.kubeflow_run_bindings_guard]
}
