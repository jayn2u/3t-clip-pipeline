variable "kubeconfig_path" {
  description = "Path to the kubeconfig produced by ansible/roles/k3s_server."
  type        = string
  default     = "./generated/kubeconfig"
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
  default     = "0.42.3"
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
}

variable "tailscale_oauth_client_secret" {
  description = "Tailscale OAuth client secret for the Kubernetes operator."
  type        = string
  sensitive   = true
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
    cache_root        = string
    cache_capacity_gi  = number
    minio_role        = string
    minio_data_root   = string # must match ansible/roles/local_storage minio_data_root for this host
  }))
  default = {
    vis-lab = {
      cache_root        = "/mnt/data/labclip-cache"
      cache_capacity_gi = 200
      minio_role        = "minio-code"
      minio_data_root   = "/mnt/data/minio-data"
    }
    ubuntu = {
      cache_root        = "/data/jayn2u/labclip-cache"
      cache_capacity_gi = 800
      minio_role        = "minio-ml-assets"
      minio_data_root   = "/data/jayn2u/minio-data"
    }
  }
}

variable "minio_root_user" {
  description = "MinIO root user, shared by both MinIO deployments."
  type        = string
  sensitive   = true
}

variable "minio_root_password" {
  description = "MinIO root password, shared by both MinIO deployments."
  type        = string
  sensitive   = true
}
