locals {
  kubeflow_run_bindings_enabled    = var.platform_mode == "kubeflow" && var.enable_kubeflow_run_bindings
  kubeflow_gpu_runtime_policy_name = "labclip-kubeflow-gpu-runtime"
  kubeflow_gpu_runtime_policy_yaml = yamlencode({
    apiVersion = "admissionregistration.k8s.io/v1"
    kind       = "MutatingAdmissionPolicy"
    metadata = {
      name = local.kubeflow_gpu_runtime_policy_name
    }
    spec = {
      failurePolicy      = "Fail"
      reinvocationPolicy = "IfNeeded"
      matchConstraints = {
        resourceRules = [
          {
            apiGroups   = [""]
            apiVersions = ["v1"]
            operations  = ["CREATE"]
            resources   = ["pods"]
          }
        ]
      }
      matchConditions = [
        {
          name       = "labclip-run-namespace"
          expression = "request.namespace == ${jsonencode(var.labclip_run_namespace)}"
        },
        {
          name       = "gpu-request-present"
          expression = <<-CEL
            object.spec.containers.exists(container,
              has(container.resources) &&
              has(container.resources.requests) &&
              "nvidia.com/gpu" in container.resources.requests
            ) || (
              has(object.spec.initContainers) &&
              object.spec.initContainers.exists(container,
                has(container.resources) &&
                has(container.resources.requests) &&
                "nvidia.com/gpu" in container.resources.requests
              )
            )
          CEL
        },
        {
          name       = "runtime-class-absent"
          expression = "!has(object.spec.runtimeClassName)"
        }
      ]
      mutations = [
        {
          patchType = "ApplyConfiguration"
          applyConfiguration = {
            expression = <<-CEL
              Object{
                spec: Object.spec{
                  runtimeClassName: "nvidia"
                }
              }
            CEL
          }
        }
      ]
    }
  })
  kubeflow_gpu_runtime_binding_yaml = yamlencode({
    apiVersion = "admissionregistration.k8s.io/v1"
    kind       = "MutatingAdmissionPolicyBinding"
    metadata = {
      name = "${local.kubeflow_gpu_runtime_policy_name}-binding"
    }
    spec = {
      policyName = local.kubeflow_gpu_runtime_policy_name
    }
  })
}

data "kubernetes_resource" "kubeflow_cache" {
  for_each = local.kubeflow_run_bindings_enabled ? var.nodes : {}

  api_version = "v1"
  kind        = "PersistentVolume"

  metadata {
    name = each.value.cache_claim
  }
}

data "kubernetes_resources" "kubeflow_cache_claims" {
  count = local.kubeflow_run_bindings_enabled ? 1 : 0

  api_version = "v1"
  kind        = "PersistentVolumeClaim"
  namespace   = var.labclip_run_namespace
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
            try(
              length([
                for claim_ref_value in values(data.kubernetes_resource.kubeflow_cache[node_name].object["spec"]["claimRef"]) : claim_ref_value
                if claim_ref_value != null
              ]) == 0,
              true
            )
            ) || (
            data.kubernetes_resource.kubeflow_cache[node_name].object["status"]["phase"] == "Bound" &&
            try(data.kubernetes_resource.kubeflow_cache[node_name].object["spec"]["claimRef"]["namespace"], "") == var.labclip_run_namespace &&
            try(data.kubernetes_resource.kubeflow_cache[node_name].object["spec"]["claimRef"]["name"], "") == node.cache_claim &&
            length([
              for claim in data.kubernetes_resources.kubeflow_cache_claims[0].objects : claim
              if claim["metadata"]["name"] == node.cache_claim &&
              claim["metadata"]["namespace"] == var.labclip_run_namespace &&
              try(claim["metadata"]["uid"], "") == try(data.kubernetes_resource.kubeflow_cache[node_name].object["spec"]["claimRef"]["uid"], "") &&
              try(claim["spec"]["volumeName"], "") == node.cache_claim
            ]) == 1
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
          length(keys(data.kubernetes_resource.kubeflow_cache[node_name].object["spec"]["nodeAffinity"])) == 1 &&
          length(keys(data.kubernetes_resource.kubeflow_cache[node_name].object["spec"]["nodeAffinity"]["required"])) == 1 &&
          length(data.kubernetes_resource.kubeflow_cache[node_name].object["spec"]["nodeAffinity"]["required"]["nodeSelectorTerms"]) == 1 &&
          contains(keys(data.kubernetes_resource.kubeflow_cache[node_name].object["spec"]["nodeAffinity"]["required"]["nodeSelectorTerms"][0]), "matchExpressions") &&
          alltrue([
            for term_key in keys(data.kubernetes_resource.kubeflow_cache[node_name].object["spec"]["nodeAffinity"]["required"]["nodeSelectorTerms"][0]) : contains(["matchExpressions", "matchFields"], term_key)
          ]) &&
          try(length(data.kubernetes_resource.kubeflow_cache[node_name].object["spec"]["nodeAffinity"]["required"]["nodeSelectorTerms"][0]["matchFields"]), 0) == 0 &&
          length(data.kubernetes_resource.kubeflow_cache[node_name].object["spec"]["nodeAffinity"]["required"]["nodeSelectorTerms"][0]["matchExpressions"]) == 1 &&
          length(keys(data.kubernetes_resource.kubeflow_cache[node_name].object["spec"]["nodeAffinity"]["required"]["nodeSelectorTerms"][0]["matchExpressions"][0])) == 3 &&
          length(data.kubernetes_resource.kubeflow_cache[node_name].object["spec"]["nodeAffinity"]["required"]["nodeSelectorTerms"][0]["matchExpressions"][0]["values"]) == 1 &&
          data.kubernetes_resource.kubeflow_cache[node_name].object["spec"]["nodeAffinity"]["required"]["nodeSelectorTerms"][0]["matchExpressions"][0]["key"] == "kubernetes.io/hostname" &&
          data.kubernetes_resource.kubeflow_cache[node_name].object["spec"]["nodeAffinity"]["required"]["nodeSelectorTerms"][0]["matchExpressions"][0]["operator"] == "In" &&
          data.kubernetes_resource.kubeflow_cache[node_name].object["spec"]["nodeAffinity"]["required"]["nodeSelectorTerms"][0]["matchExpressions"][0]["values"][0] == node_name,
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

resource "kubectl_manifest" "kubeflow_gpu_runtime_policy" {
  count = local.kubeflow_run_bindings_enabled ? 1 : 0

  yaml_body         = local.kubeflow_gpu_runtime_policy_yaml
  server_side_apply = true

  depends_on = [terraform_data.kubeflow_run_bindings_guard]
}

resource "kubectl_manifest" "kubeflow_gpu_runtime_binding" {
  count = local.kubeflow_run_bindings_enabled ? 1 : 0

  yaml_body         = local.kubeflow_gpu_runtime_binding_yaml
  server_side_apply = true

  depends_on = [
    kubectl_manifest.kubeflow_gpu_runtime_policy,
    terraform_data.kubeflow_run_bindings_guard,
  ]
}
