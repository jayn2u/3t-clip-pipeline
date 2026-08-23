# LabCLIP test portability map

The authoritative classification is `labclip-test-classification.yaml`. Discovery is fresh from `/mnt/data/lab_clip/pipeline/tests/test_*.py`; each basename must occur exactly once. The ledger is provenance only: target code never imports or executes the reference repository.

| Class | Reference tests | Portable boundary |
| --- | --- | --- |
| Portable invariant | `test_argo_workflows_manifests.py`, `test_cache_profile.py`, `test_code_bundle.py`, `test_dataset_cache.py`, `test_gpu_scheduling.py`, `test_layout.py`, `test_result_sync.py`, `test_s3_integrity.py`, `test_train_terminal_dag.py` | Renderer lifecycle, scheduler accounting, bundle safety, inventory/hydration, publication retry/marker eligibility, S3 verification, and secret redaction are reimplemented against the new contract. |
| LabCLIP-only | `test_local_cache_manifests.py`, `test_minio_bootstrap.py`, `test_minio_manifests.py`, `test_minio_ml_assets_naming.py`, `test_option_c_paths.py`, `test_storage.py`, `test_submit_core.py`, `test_submit_kfold.py`, `test_tailscale_operator_manifests.py` | Fixed datasets, training entries, W&B rules, node/PV/PVC/bucket/service names, `labclip.*` labels, five-fold matrices, and Tailscale hostnames are parameterized or excluded. |

Portable means behavioral provenance, not copied source. Later ports cite the originating file/range and assert outcomes through the target package's public surface.
