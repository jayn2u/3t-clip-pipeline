# LabCLIP test portability map

The authoritative classification is `labclip-test-classification.yaml`. Discovery is fresh from `/mnt/data/lab_clip/pipeline/tests/test_*.py`; each basename occurs exactly once. The ledger records provenance only: target code never imports or executes the reference repository.

<!-- test-map:v1 portable: 9 consumer-owned: 12 -->

| Reference test | Classification | Target-owned test | Consumer adapter hook |
| --- | --- | --- | --- |
| `test_argo_workflows_manifests.py` | portable-invariant | `tests/portable/test_rendering.py::test_renderer_preserves_generic_consumer_and_scheduler_contract` | — |
| `test_cache_profile.py` | portable-invariant | `tests/portable/test_profiles.py::test_profile_names_are_generic_and_lease_is_released_before_wait` | — |
| `test_code_bundle.py` | portable-invariant | `tests/portable/test_bundle_cache.py::test_bundle_is_deterministic_and_excludes_nonportable_paths` | — |
| `test_dataset_cache.py` | portable-invariant | `tests/portable/test_bundle_cache.py::test_inventory_hydration_uses_generic_store_and_atomic_destination` | — |
| `test_download_run_model.py` | labclip-only | — | application artifact-download adapter |
| `test_gpu_scheduling.py` | portable-invariant | `tests/portable/test_rendering.py::test_renderer_preserves_generic_consumer_and_scheduler_contract` | — |
| `test_layout.py` | portable-invariant | `tests/portable/test_boundary.py::test_target_import_graph_has_no_reference_or_consumer_packages` | — |
| `test_local_cache_manifests.py` | labclip-only | — | deployment storage adapter |
| `test_minio_bootstrap.py` | labclip-only | — | object-store bootstrap adapter |
| `test_minio_manifests.py` | labclip-only | — | deployment object-store adapter |
| `test_minio_ml_assets_naming.py` | labclip-only | — | consumer object-key adapter |
| `test_option_c_paths.py` | labclip-only | — | consumer cache-path adapter |
| `test_post_eval_runner.py` | labclip-only | — | application command adapter |
| `test_result_sync.py` | portable-invariant | `tests/portable/test_publication.py::test_verified_objects_publish_one_canonical_marker` | — |
| `test_run_artifacts.py` | labclip-only | — | required-output-path adapter |
| `test_s3_integrity.py` | portable-invariant | `tests/portable/test_publication.py::test_head_mismatch_blocks_marker` | — |
| `test_storage.py` | labclip-only | — | storage client adapter |
| `test_submit_core.py` | labclip-only | — | standalone consumer workload adapter |
| `test_submit_train.py` | labclip-only | — | consumer command adapter |
| `test_tailscale_operator_manifests.py` | labclip-only | — | deployment ingress adapter |
| `test_train_terminal_dag.py` | portable-invariant | `tests/portable/test_rendering.py::test_renderer_preserves_generic_consumer_and_scheduler_contract` | — |

Portable means behavioral provenance, not copied source. The target links assert outcomes through public platform surfaces with generic names, resources, stores, and commands. Consumer-owned rows are intentionally not ported; each names the narrow adapter seam where an application or deployment can supply that policy without expanding the platform contract.
