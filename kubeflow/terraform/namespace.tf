resource "kubernetes_namespace" "argo" {
  metadata {
    name = var.argo_namespace
  }
}
