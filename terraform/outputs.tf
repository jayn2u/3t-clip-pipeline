output "argo_namespace" {
  value = kubernetes_namespace.argo.metadata[0].name
}

output "minio_services" {
  description = "In-cluster DNS names for each MinIO deployment."
  value = {
    for k, v in var.nodes :
    v.minio_role => "${v.minio_role}.${kubernetes_namespace.minio.metadata[0].name}.svc.cluster.local"
  }
}

output "cache_persistent_volumes" {
  value = { for k, v in kubernetes_persistent_volume.cache : k => v.metadata[0].name }
}
