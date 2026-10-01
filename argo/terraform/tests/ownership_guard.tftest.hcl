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

run "fresh_cluster_passes" {
  command = plan
}

run "unlabelled_objects_pass" {
  command = plan

  override_data {
    target = data.kubernetes_resources.ownership_namespace
    values = {
      objects = [{ metadata = { name = "argo" } }]
    }
  }

  override_data {
    target = data.kubernetes_resources.ownership_storage_class
    values = {
      objects = [{ metadata = { name = "labclip-local-cache" } }]
    }
  }
}

run "objects_owned_by_this_stack_pass" {
  command = plan

  override_data {
    target = data.kubernetes_resources.ownership_namespace
    values = {
      objects = [{ metadata = { name = "argo", labels = { "labclip.io/iac-stack" = "argo" } } }]
    }
  }

  override_data {
    target = data.kubernetes_resources.ownership_storage_class
    values = {
      objects = [{ metadata = { name = "labclip-local-cache", labels = { "labclip.io/iac-stack" = "argo" } } }]
    }
  }
}

run "namespace_owned_by_the_other_stack_fails" {
  command = plan

  override_data {
    target = data.kubernetes_resources.ownership_namespace
    values = {
      objects = [{ metadata = { name = "argo", labels = { "labclip.io/iac-stack" = "kubeflow" } } }]
    }
  }

  expect_failures = [terraform_data.ownership_guard]
}

run "storage_class_owned_by_the_other_stack_fails" {
  command = plan

  override_data {
    target = data.kubernetes_resources.ownership_storage_class
    values = {
      objects = [{ metadata = { name = "labclip-local-cache", labels = { "labclip.io/iac-stack" = "kubeflow" } } }]
    }
  }

  expect_failures = [terraform_data.ownership_guard]
}

run "other_stack_in_a_differently_named_namespace_is_caught_through_the_storage_class" {
  command = plan

  variables {
    argo_namespace = "custom-namespace"
  }

  override_data {
    target = data.kubernetes_resources.ownership_storage_class
    values = {
      objects = [{ metadata = { name = "labclip-local-cache", labels = { "labclip.io/iac-stack" = "kubeflow" } } }]
    }
  }

  expect_failures = [terraform_data.ownership_guard]
}
