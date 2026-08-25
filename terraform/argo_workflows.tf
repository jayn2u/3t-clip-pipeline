# Replaces the hand-applied pipeline/k8s/argo-workflows/{00,10,20,30,40}-*.yaml
# (including the 11MB CRD bundle) with the upstream Helm chart, which owns and
# versions its own CRDs. This removes the biggest source of "did I apply the
# CRDs before the controller" ordering drift from the old manual procedure.

resource "helm_release" "argo_workflows" {
  name       = "argo-workflows"
  repository = "https://argoproj.github.io/argo-helm"
  chart      = "argo-workflows"
  version    = var.argo_workflows_chart_version
  namespace  = kubernetes_namespace.argo.metadata[0].name

  values = [
    yamlencode({
      controller = {
        workflowNamespaces = [kubernetes_namespace.argo.metadata[0].name]
      }
      server = {
        # Server is reached via Tailscale ingress (tailscale.tf), never a public LB.
        serviceType = "ClusterIP"
      }
      crds = {
        install = true
        keep    = true # don't delete CRDs (and therefore live Workflow objects) on chart uninstall
      }
    })
  ]

  depends_on = [kubernetes_namespace.argo]
}
