resource "kubernetes_namespace" "argo" {
  metadata {
    name = var.argo_namespace
  }
}

resource "kubernetes_namespace" "minio" {
  metadata {
    name = "minio"
  }
}
