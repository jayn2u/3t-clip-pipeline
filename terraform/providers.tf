# All three providers point at the kubeconfig Ansible's k3s_server role fetched
# to generated/kubeconfig. There is no cloud provider here - this is a bare-metal
# k3s cluster, so Terraform's only job is managing resources on an API server that
# already exists (see ../ansible/README.md for how that server comes to exist).

provider "kubernetes" {
  config_path    = var.kubeconfig_path
  config_context = var.kube_context
}

provider "helm" {
  kubernetes {
    config_path    = var.kubeconfig_path
    config_context = var.kube_context
  }
}

provider "kubectl" {
  config_path      = var.kubeconfig_path
  config_context   = var.kube_context
  load_config_file = true
}
