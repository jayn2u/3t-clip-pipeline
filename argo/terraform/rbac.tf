resource "kubernetes_service_account" "labclip_runner" {
  metadata {
    name      = "labclip-runner"
    namespace = kubernetes_namespace.argo.metadata[0].name
  }
}

resource "kubernetes_cluster_role" "labclip_runner" {
  metadata {
    name = "labclip-runner"
  }

  rule {
    api_groups = [""]
    resources  = ["nodes"]
    verbs      = ["get", "list"]
  }

  rule {
    api_groups = [""]
    resources  = ["pods"]
    verbs      = ["get", "list", "create", "watch"]
  }

  rule {
    api_groups = ["coordination.k8s.io"]
    resources  = ["leases"]
    verbs      = ["get", "create", "update"]
  }

  rule {
    api_groups = ["argoproj.io"]
    resources  = ["workflows", "workflowtemplates", "workflowtaskresults"]
    verbs      = ["get", "list", "watch", "create", "patch", "update"]
  }
}

resource "kubernetes_cluster_role_binding" "labclip_runner" {
  metadata {
    name = "labclip-runner"
  }

  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "ClusterRole"
    name      = kubernetes_cluster_role.labclip_runner.metadata[0].name
  }

  subject {
    kind      = "ServiceAccount"
    name      = kubernetes_service_account.labclip_runner.metadata[0].name
    namespace = kubernetes_namespace.argo.metadata[0].name
  }
}

resource "kubernetes_role" "argo_workflowtaskresults_cleanup" {
  metadata {
    name      = "argo-workflowtaskresults-cleanup"
    namespace = kubernetes_namespace.argo.metadata[0].name
  }

  rule {
    api_groups = ["argoproj.io"]
    resources  = ["workflowtaskresults"]
    verbs      = ["get", "list", "watch", "delete", "deletecollection"]
  }
}

resource "kubernetes_role_binding" "argo_workflowtaskresults_cleanup" {
  metadata {
    name      = kubernetes_role.argo_workflowtaskresults_cleanup.metadata[0].name
    namespace = kubernetes_namespace.argo.metadata[0].name
  }

  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "Role"
    name      = kubernetes_role.argo_workflowtaskresults_cleanup.metadata[0].name
  }

  subject {
    kind      = "ServiceAccount"
    name      = "argo"
    namespace = kubernetes_namespace.argo.metadata[0].name
  }
}
