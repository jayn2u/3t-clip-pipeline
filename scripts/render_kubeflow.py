import argparse
from pathlib import Path

from prepare_kubeflow_overlay import (
    OVERLAY_ROOT,
    UPSTREAM_SOURCE_REF,
    render_distribution,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-ref", default=UPSTREAM_SOURCE_REF)
    parser.add_argument("--overlay-dir", type=Path, default=OVERLAY_ROOT)
    parser.add_argument(
        "--output",
        type=Path,
        default=REPOSITORY_ROOT / "kubeflow/generated/rendered.yaml",
    )
    args = parser.parse_args()
    receipt = render_distribution(args.source_ref, args.overlay_dir, args.output)
    print(f"Rendered {len(receipt.inventory)} Kubeflow objects; SHA-256 {receipt.sha256}.")
    print(f"Manifest: {receipt.output_path}")
    print(f"Receipt: {receipt.receipt_path}")
    print(f"Inventory: {receipt.inventory_path}")


if __name__ == "__main__":
    main()
