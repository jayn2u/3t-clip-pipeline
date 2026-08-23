"""Command-line entry point for the platform package."""

from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from three_t_clip_pipeline import __version__
from three_t_clip_pipeline.contract import canonical_json_bytes, load_workload, validation_codes
from three_t_clip_pipeline.contract.io import WorkloadReadError, WorkloadYamlError

app = typer.Typer(
    name="3t-pipeline",
    help="Build and operate portable 3T-CLIP pipeline workloads.",
    no_args_is_help=True,
)
contract_app = typer.Typer(help="Validate frozen workload contracts.", no_args_is_help=True)
app.add_typer(contract_app, name="contract")


@app.callback(invoke_without_command=True)
def root(
    *,
    short: Annotated[
        bool, typer.Option(help="Print only the version number and exit.", is_eager=True)
    ] = False,
) -> None:
    """Build and operate portable pipeline workloads."""
    if short:
        typer.echo(__version__)
        raise typer.Exit


@app.command()
def version(
    *,
    short: Annotated[bool, typer.Option(help="Print only the version number.")] = False,
) -> None:
    """Show the installed platform version."""
    typer.echo(__version__ if short else f"3t-pipeline {__version__}")


@contract_app.command("validate")
def validate_contract(
    workload_path: Annotated[Path, typer.Argument(exists=False, dir_okay=False)],
    *,
    canonical_json: Annotated[
        Path | None,
        typer.Option(help="Write canonical JSON with defaults to this path."),
    ] = None,
) -> None:
    """Validate a v1alpha1 workload and optionally write canonical JSON."""
    try:
        workload = load_workload(workload_path)
    except ValidationError as error:
        typer.echo(f"CONTRACT_INVALID {','.join(validation_codes(error))}", err=True)
        raise typer.Exit(code=2) from error
    except (WorkloadReadError, WorkloadYamlError) as error:
        typer.echo(f"CONTRACT_INVALID input_error: {error}", err=True)
        raise typer.Exit(code=2) from error
    if canonical_json is not None:
        canonical_json.parent.mkdir(parents=True, exist_ok=True)
        _ = canonical_json.write_bytes(canonical_json_bytes(workload))
    typer.echo("CONTRACT_OK")


def main() -> None:
    """Run the platform command-line application."""
    app()
