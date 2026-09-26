resource "helm_release" "nvidia_device_plugin" {
  name             = "nvdp"
  repository       = "https://nvidia.github.io/k8s-device-plugin"
  chart            = "nvidia-device-plugin"
  version          = "0.19.3"
  namespace        = "nvidia-device-plugin"
  create_namespace = true
  wait             = true
  timeout          = 600

  values = [
    yamlencode({
      runtimeClassName = "nvidia"
      nfd = {
        enabled = true
      }
      gfd = {
        enabled = true
        securityContext = {
          privileged = true
        }
      }
      deviceListStrategy = "envvar"
      deviceIDStrategy   = "uuid"
      failOnInitError    = true
      nvidiaDriverRoot   = "/"
    })
  ]
}
