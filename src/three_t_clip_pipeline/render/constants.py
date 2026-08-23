"""Frozen renderer and runtime boundary constants."""

from typing import Final

ARTIFACT_PUBLISHER_NAME: Final = "artifact-publisher"
BUNDLE_INIT_NAME: Final = "bundle-init"
COMMIT_RESULTS_NAME: Final = "commit-results"
MAIN_NAME: Final = "main"

CACHE_MOUNT: Final = "/workspace/cache"
CONTROL_MOUNT: Final = "/workspace/control"
INPUT_MOUNT: Final = "/workspace/input"
OUTPUT_MOUNT: Final = "/workspace/output"

COMMIT_MARKER_NAME: Final = "COMMITTED.json"
INIT_MANIFEST_NAME: Final = "init.json"
PUBLICATION_MANIFEST_NAME: Final = "publication.json"

PLATFORM_IMAGE: Final = (
    "docker.io/library/python:3.12.11-slim-bookworm@sha256:"
    "519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7"
)
NAMESPACE: Final = "three-t-pipeline"
SERVICE_ACCOUNT: Final = "pipeline-runner"
