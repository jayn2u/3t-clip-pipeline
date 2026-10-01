resource "kubernetes_namespace" "argo" {
  metadata {
    name = var.argo_namespace
    labels = {
      "labclip.io/iac-stack" = local.iac_stack_name
    }
  }

  depends_on = [terraform_data.ownership_guard]
}
