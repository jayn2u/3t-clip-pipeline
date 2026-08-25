# Optional per docs/cluster/10-install-tailscale-optional.md. Gated behind
# enable_tailscale so clusters that don't need private remote access can skip
# it entirely, matching the "not required for core execution" status in
# docs/cluster/00-current-architecture.md.

resource "kubernetes_namespace" "tailscale" {
  count = var.enable_tailscale ? 1 : 0

  metadata {
    name = "tailscale"
  }
}

resource "kubernetes_secret" "tailscale_oauth" {
  count = var.enable_tailscale ? 1 : 0

  metadata {
    name      = "operator-oauth"
    namespace = kubernetes_namespace.tailscale[0].metadata[0].name
  }

  data = {
    client_id     = var.tailscale_oauth_client_id
    client_secret = var.tailscale_oauth_client_secret
  }
}

resource "helm_release" "tailscale_operator" {
  count = var.enable_tailscale ? 1 : 0

  name       = "tailscale-operator"
  repository = "https://pkgs.tailscale.com/helmcharts"
  chart      = "tailscale-operator"
  version    = var.tailscale_operator_chart_version
  namespace  = kubernetes_namespace.tailscale[0].metadata[0].name

  values = [
    yamlencode({
      oauth = {
        clientId     = { valueFrom = { secretKeyRef = { name = kubernetes_secret.tailscale_oauth[0].metadata[0].name, key = "client_id" } } }
        clientSecret = { valueFrom = { secretKeyRef = { name = kubernetes_secret.tailscale_oauth[0].metadata[0].name, key = "client_secret" } } }
      }
    })
  ]

  depends_on = [kubernetes_secret.tailscale_oauth]
}
