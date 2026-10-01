variable "kubeconfig_path" {
  description = "Path to the kubeconfig produced by ansible/roles/k3s_server."
  type        = string
  default     = "../../ansible/generated/kubeconfig"
}

variable "kube_context" {
  description = "kubeconfig context to use. Null uses the current-context."
  type        = string
  default     = null
}

variable "argo_namespace" {
  description = "Namespace Argo Workflows is installed into."
  type        = string
  default     = "argo"
}

variable "argo_workflows_chart_version" {
  description = "Pinned argo-workflows Helm chart version."
  type        = string
  default     = "1.0.20"
}

variable "labclip_workflow_template_path" {
  description = "Path to the generated LabCLIP WorkflowTemplate YAML, relative to the Terraform root."
  type        = string
  default     = "../../../lab_clip/pipeline/k8s/generated/labclip-train.yaml"
}

variable "labclip_root" {
  description = "LabCLIP checkout containing the reusable MinIO bootstrap CLI and Python environment."
  type        = string
  default     = "/mnt/data/lab_clip"
}

variable "tailscale_operator_chart_version" {
  description = "Pinned tailscale-operator Helm chart version."
  type        = string
  default     = "1.76.1"
}

variable "tailscale_oauth_client_id" {
  description = "Tailscale OAuth client ID for the Kubernetes operator."
  type        = string
  sensitive   = true
  default     = ""
}

variable "tailscale_oauth_client_secret" {
  description = "Tailscale OAuth client secret for the Kubernetes operator."
  type        = string
  sensitive   = true
  default     = ""
}

variable "enable_tailscale" {
  description = "Whether to install the Tailscale operator (optional per docs/cluster/10)."
  type        = bool
  default     = false
}

variable "nodes" {
  description = <<-EOT
    Per-node cache/MinIO layout. Keys must match k3s node names produced by the
    Ansible inventory in ../ansible/inventory/hosts.yml.
  EOT
  type = map(object({
    cache_root              = string
    cache_capacity_gi       = number
    cache_claim_request_gi  = number
    cache_claim             = string
    minio_role              = string
    minio_store             = string
    minio_data_root         = string
    minio_host_port         = number
    minio_external_endpoint = string
  }))
  default = {
    vis-lab = {
      cache_root              = "/mnt/data/labclip-cache"
      cache_capacity_gi       = 200
      cache_claim_request_gi  = 100
      cache_claim             = "labclip-cache-vis-lab"
      minio_role              = "minio-code"
      minio_store             = "code"
      minio_data_root         = "/mnt/data/minio-code"
      minio_host_port         = 3910
      minio_external_endpoint = "http://127.0.0.1:3910"
    }
    ubuntu = {
      cache_root              = "/data/jayn2u/labclip-cache"
      cache_capacity_gi       = 800
      cache_claim_request_gi  = 100
      cache_claim             = "labclip-cache-ubuntu"
      minio_role              = "minio-ml-assets"
      minio_store             = "ml-assets"
      minio_data_root         = "/data/jayn2u/minio"
      minio_host_port         = null
      minio_external_endpoint = ""
    }
  }
}

variable "minio_root_user" {
  description = "MinIO root user, shared by both MinIO deployments."
  type        = string
  sensitive   = true

  validation {
    condition = (
      length(var.minio_root_user) >= 16 &&
      !strcontains(var.minio_root_user, "minioadmin") &&
      !startswith(var.minio_root_user, "-")
    )
    error_message = "MinIO root user must be at least 16 characters, must not contain the default username, and must not begin with a dash."
  }
}

variable "minio_root_password" {
  description = "MinIO root password, shared by both MinIO deployments."
  type        = string
  sensitive   = true

  validation {
    condition = (
      length(var.minio_root_password) >= 16 &&
      !strcontains(var.minio_root_password, "minioadmin") &&
      !startswith(var.minio_root_password, "-")
    )
    error_message = "MinIO root password must be at least 16 characters, must not contain the default password, and must not begin with a dash."
  }
}

variable "minio_credentials" {
  description = "Stable non-root pipeline and researcher credentials for both MinIO stores."
  type = map(object({
    access_key            = string
    secret_key            = string
    researcher_access_key = string
    researcher_secret_key = string
  }))
  sensitive = true

  validation {
    condition = alltrue([
      for credentials in values(var.minio_credentials) :
      length(credentials.access_key) >= 16 &&
      length(credentials.secret_key) >= 16 &&
      !startswith(credentials.access_key, "-") &&
      !startswith(credentials.secret_key, "-") &&
      !strcontains(credentials.access_key, "minioadmin") &&
      !strcontains(credentials.secret_key, "minioadmin") &&
      length(credentials.researcher_access_key) >= 8 &&
      length(credentials.researcher_access_key) <= 40 &&
      !startswith(credentials.researcher_access_key, "-") &&
      !strcontains(credentials.researcher_access_key, "minioadmin") &&
      length(credentials.researcher_secret_key) >= 8 &&
      length(credentials.researcher_secret_key) <= 40 &&
      !startswith(credentials.researcher_secret_key, "-") &&
      !strcontains(credentials.researcher_secret_key, "minioadmin")
    ])
    error_message = "Pipeline MinIO credentials must be at least 16 characters and researcher credentials 8 to 40 characters; none may begin with a dash or contain the default username."
  }
}

variable "ghcr_dockerconfigjson" {
  description = "Docker config JSON containing only the ghcr.io authentication entry."
  type        = string
  sensitive   = true
}

variable "wandb_api_key" {
  description = "W&B API key consumed by LabCLIP training and evaluation steps."
  type        = string
  sensitive   = true
}

variable "wandb_entity" {
  description = "W&B entity consumed by LabCLIP training and evaluation steps."
  type        = string
  sensitive   = true
}

variable "wandb_project" {
  description = "W&B project consumed by LabCLIP training and evaluation steps."
  type        = string
  sensitive   = true
}
