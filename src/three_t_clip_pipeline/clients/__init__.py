"""Typed external clients used by operator commands."""

from three_t_clip_pipeline.clients.errors import ClientExecutionError
from three_t_clip_pipeline.clients.fake import FakeKubernetesClient
from three_t_clip_pipeline.clients.kubernetes import KubectlClient
from three_t_clip_pipeline.clients.types import ClientCall, KubernetesClient

__all__ = [
    "ClientCall",
    "ClientExecutionError",
    "FakeKubernetesClient",
    "KubectlClient",
    "KubernetesClient",
]
