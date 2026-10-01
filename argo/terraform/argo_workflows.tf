resource "helm_release" "argo_workflows" {
  name       = "argo-workflows"
  repository = "https://argoproj.github.io/argo-helm"
  chart      = "argo-workflows"
  version    = var.argo_workflows_chart_version
  namespace  = kubernetes_namespace.argo.metadata[0].name

  values = [
    yamlencode({
      fullnameOverride = "argo"
      controller = {
        workflowNamespaces = [kubernetes_namespace.argo.metadata[0].name]
        serviceAccount = {
          create = true
          name   = "argo"
        }
      }
      server = {
        serviceType = "ClusterIP"
        serviceAccount = {
          create = true
          name   = "argo-server"
        }
      }
      crds = {
        install = true
        keep    = true
      }
    })
  ]

  depends_on = [kubernetes_namespace.argo]
}

resource "kubectl_manifest" "labclip_train" {
  yaml_body         = file(var.labclip_workflow_template_path)
  server_side_apply = true

  depends_on = [
    helm_release.argo_workflows,
    helm_release.nvidia_device_plugin,
    kubernetes_secret.ghcr,
    kubernetes_secret.minio_credentials,
    kubernetes_secret.minio_researcher_credentials,
    kubernetes_secret.wandb,
    kubernetes_service_account.labclip_runner,
  ]
}
