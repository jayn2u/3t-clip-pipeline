import argparse
from pathlib import Path
import re

from prepare_kubeflow_overlay import (
    OVERLAY_ROOT,
    UPSTREAM_SOURCE_REF,
    approve_render,
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
    parser.add_argument(
        "--approve-digest",
        help="Explicitly approve this exact SHA-256 digest after reviewing the candidate render.",
    )
    args = parser.parse_args()
    if args.approve_digest is not None and re.fullmatch(r"[0-9a-f]{64}", args.approve_digest) is None:
        parser.error("--approve-digest must be exactly 64 lowercase hexadecimal characters.")
    try:
        receipt = render_distribution(
            args.source_ref,
            args.overlay_dir,
            args.output,
            enforce_approval=args.approve_digest is None,
        )
        if args.approve_digest is not None:
            approval = approve_render(
                args.approve_digest,
                receipt.output_path,
                receipt.receipt_path,
                receipt.inventory_path,
                receipt.approval_path,
            )
            print(
                f"Approved Kubeflow digest {approval['sha256']} for {approval['object_count']} objects."
            )
        elif receipt.approved:
            print(f"Rendered approved Kubeflow digest {receipt.sha256}.")
        else:
            print(
                f"Rendered candidate digest {receipt.sha256} for {len(receipt.inventory)} objects; approval is required before apply."
            )
        print(f"Manifest: {receipt.output_path}")
        print(f"Receipt: {receipt.receipt_path}")
        print(f"Inventory: {receipt.inventory_path}")
        print(f"Approval: {receipt.approval_path}")
        if args.approve_digest is None and not receipt.approved:
            print(
                f"Review the candidate and approve explicitly with --approve-digest {receipt.sha256}."
            )
    except (OSError, RuntimeError, ValueError) as exc:
        parser.exit(2, f"error: {exc}\n")


if __name__ == "__main__":
    main()
