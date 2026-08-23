"""Generate the checked-in Draft 2020-12 workload schema."""

from __future__ import annotations

import json
from pathlib import Path  # noqa: TC003 - Typer resolves annotations at runtime.
from typing import Annotated, Final

import typer

from three_t_clip_pipeline.contract.models import API_VERSION, Workload

SCHEMA_DIALECT: Final = "https://json-schema.org/draft/2020-12/schema"
SCHEMA_ID: Final = "https://schemas.three-t.dev/workload-v1alpha1.schema.json"


def schema_bytes() -> bytes:
    """Return deterministic checked-in schema bytes."""
    schema = Workload.model_json_schema(by_alias=True, mode="validation")
    schema["$schema"] = SCHEMA_DIALECT
    schema["$id"] = SCHEMA_ID
    schema["title"] = f"{API_VERSION} Workload"
    return (json.dumps(schema, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def main(
    output: Path,
    *,
    check: Annotated[
        bool, typer.Option(help="Fail if OUTPUT differs from generated schema.")
    ] = False,
) -> None:
    """Write or verify the canonical workload JSON Schema."""
    expected = schema_bytes()
    if check:
        try:
            actual = output.read_bytes()
        except OSError as error:
            typer.echo(f"schema_read_error: {error}", err=True)
            raise typer.Exit(code=2) from error
        if actual != expected:
            typer.echo(f"schema_out_of_date: {output}", err=True)
            raise typer.Exit(code=1)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    _ = output.write_bytes(expected)


if __name__ == "__main__":
    typer.run(main)
