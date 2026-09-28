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

  lifecycle {
    precondition {
      condition = nonsensitive(
        trimspace(var.tailscale_oauth_client_id) != "" &&
        trimspace(var.tailscale_oauth_client_secret) != ""
      )
      error_message = "Set both Tailscale OAuth values through the private Terraform inputs before enabling the operator."
    }
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
