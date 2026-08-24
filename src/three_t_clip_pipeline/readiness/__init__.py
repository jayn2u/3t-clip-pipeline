"""Typed, read-only PV and MinIO cutover readiness evaluation."""

from three_t_clip_pipeline.readiness.evaluator import evaluate_readiness
from three_t_clip_pipeline.readiness.models import (
    EvidenceKind,
    EvidenceRecord,
    ReadinessBundle,
    ReadinessCheck,
    ReadinessReport,
)

__all__ = [
    "EvidenceKind",
    "EvidenceRecord",
    "ReadinessBundle",
    "ReadinessCheck",
    "ReadinessReport",
    "evaluate_readiness",
]
