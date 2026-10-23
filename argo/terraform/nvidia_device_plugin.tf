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
      nodeSelector       = var.gpu_node_selector
      tolerations = concat(
        [
          {
            key      = "CriticalAddonsOnly"
            operator = "Exists"
          },
          {
            key      = "nvidia.com/gpu"
            operator = "Exists"
            effect   = "NoSchedule"
          },
        ],
        [
          for key in keys(var.gpu_node_selector) : {
            key      = key
            operator = "Exists"
            effect   = "NoSchedule"
          }
        ],
      )
    })
  ]
}
