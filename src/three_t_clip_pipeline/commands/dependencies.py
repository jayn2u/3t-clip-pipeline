"""Replaceable construction boundary for external clients."""

from three_t_clip_pipeline.clients import KubectlClient, KubernetesClient


def kubernetes_client(context: str) -> KubernetesClient:
    """Construct a client bound to the explicitly selected context."""
    return KubectlClient(context=context)
