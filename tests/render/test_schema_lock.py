from pathlib import Path

from three_t_clip_pipeline.render import validate_schema_lock

ROOT = Path(__file__).resolve().parents[2]


def test_every_packaged_schema_matches_source_lock() -> None:
    validate_schema_lock(ROOT)


def test_validator_wrapper_uses_only_literal_local_schema_locations() -> None:
    source = (ROOT / "scripts/validate-manifest-local.sh").read_text()

    invocation = (
        '"$PWD/.cache/tools/kubeconform-v0.7.0" -strict -summary -exit-on-error '
        "-ignore-missing-schemas=false -kubernetes-version 1.36.2 "
        '-schema-location "$PWD/schemas/kubernetes/v1.36.2-standalone-strict/'
        '{{.ResourceKind}}{{.KindSuffix}}.json" -schema-location '
        '"$PWD/schemas/argo/v4.0.7/{{.ResourceKind}}-{{.ResourceAPIVersion}}.json" '
        '"$MANIFEST"'
    )
    assert invocation in source
    assert "kubectl" not in source
    assert "https://" not in source
    assert "ignore-missing-schemas=true" not in source


def test_bootstrap_freezes_archive_binary_and_install_path() -> None:
    source = (ROOT / "scripts/bootstrap-client-validator.sh").read_text()

    assert "VERSION=v0.7.0" in source
    assert "c31518ddd122663b3f3aa874cfe8178cb0988de944f29c74a0b9260920d115d3" in source
    assert "dd5273bdbf08531bf230f4eba8359984b7beec25a679424ff90911a2591b8b0d" in source
    assert 'TARGET="$PWD/.cache/tools/kubeconform-v0.7.0"' in source
