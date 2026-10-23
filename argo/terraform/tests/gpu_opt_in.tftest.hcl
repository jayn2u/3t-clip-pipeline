mock_provider "kubernetes" {}

mock_provider "helm" {}

mock_provider "kubectl" {}

variables {
  minio_root_user     = "synthetic-root-user-0001"
  minio_root_password = "synthetic-root-password-0001"
  minio_credentials = {
    code = {
      access_key            = "synthetic-code-access-01"
      secret_key            = "synthetic-code-secret-0001"
      researcher_access_key = "synth-code-rs"
      researcher_secret_key = "synth-code-rs-secret"
    }
    "ml-assets" = {
      access_key            = "synthetic-ml-access-0001"
      secret_key            = "synthetic-ml-secret-00001"
      researcher_access_key = "synth-ml-rs"
      researcher_secret_key = "synth-ml-rs-secret"
    }
  }
  ghcr_dockerconfigjson          = "{}"
  wandb_api_key                  = "synthetic-wandb-key"
  wandb_entity                   = "synthetic-entity"
  wandb_project                  = "synthetic-project"
  labclip_workflow_template_path = "versions.tf"
}

override_data {
  target = data.kubernetes_resources.ownership_namespace
  values = {
    objects = []
  }
}

override_data {
  target = data.kubernetes_resources.ownership_storage_class
  values = {
    objects = []
  }
}

run "device_plugin_only_targets_opted_in_gpu_nodes" {
  command = plan

  assert {
    condition     = yamldecode(helm_release.nvidia_device_plugin.values[0]).nodeSelector == { "labclip.io/k8s-gpu" = "true" }
    error_message = "The NVIDIA device plugin must only run on nodes labelled labclip.io/k8s-gpu=true."
  }

  assert {
    condition = contains(
      [for toleration in yamldecode(helm_release.nvidia_device_plugin.values[0]).tolerations : toleration.key],
      "labclip.io/k8s-gpu",
    )
    error_message = "The NVIDIA device plugin must tolerate the GPU opt-in taint."
  }

  assert {
    condition = contains(
      [for toleration in yamldecode(helm_release.nvidia_device_plugin.values[0]).tolerations : toleration.key],
      "nvidia.com/gpu",
    )
    error_message = "Overriding tolerations must keep the chart default nvidia.com/gpu toleration."
  }
}

run "empty_gpu_node_selector_is_rejected" {
  command = plan

  variables {
    gpu_node_selector = {}
  }

  expect_failures = [var.gpu_node_selector]
}
