"""Command-line entry point for the platform package."""

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated, Final

import typer
from pydantic import ValidationError

from three_t_clip_pipeline import __version__
from three_t_clip_pipeline.contract import canonical_json_bytes, load_workload, validation_codes
from three_t_clip_pipeline.contract.io import WorkloadReadError, WorkloadYamlError
from three_t_clip_pipeline.render import (
    ClientValidationError,
    SchemaLockError,
    render_workflow_bytes,
    run_client_validation,
)

_STDOUT_PATH: Final = Path("-")

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


@app.command("plan")
def plan_workload(
    workload_path: Annotated[Path, typer.Argument(exists=False, dir_okay=False)],
    *,
    output: Annotated[
        Path, typer.Option(help="Workflow YAML output path, or '-' for stdout.")
    ] = _STDOUT_PATH,
) -> None:
    """Render a validated workload without contacting external services."""
    try:
        workload = load_workload(workload_path)
    except ValidationError as error:
        typer.echo(f"PLAN_INVALID {','.join(validation_codes(error))}", err=True)
        raise typer.Exit(code=2) from error
    except (WorkloadReadError, WorkloadYamlError) as error:
        typer.echo(f"PLAN_INVALID input_error: {error}", err=True)
        raise typer.Exit(code=2) from error
    rendered = render_workflow_bytes(workload)
    if output == _STDOUT_PATH:
        typer.echo(rendered.decode(), nl=False)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    _ = output.write_bytes(rendered)


@app.command("validate")
def validate_workload(
    workload_path: Annotated[Path, typer.Argument(exists=False, dir_okay=False)],
    *,
    client: Annotated[bool, typer.Option(help="Use only the packaged local validator.")] = False,
) -> None:
    """Render and validate a workload with the explicitly selected validation level."""
    if not client:
        typer.echo("VALIDATION_LEVEL_REQUIRED --client", err=True)
        raise typer.Exit(code=2)
    try:
        workload = load_workload(workload_path)
    except ValidationError as error:
        typer.echo(f"CLIENT_INVALID {','.join(validation_codes(error))}", err=True)
        raise typer.Exit(code=2) from error
    except (WorkloadReadError, WorkloadYamlError) as error:
        typer.echo(f"CLIENT_INVALID input_error: {error}", err=True)
        raise typer.Exit(code=2) from error
    root = Path.cwd()
    cache = root / ".cache"
    cache.mkdir(parents=True, exist_ok=True)
    try:
        with TemporaryDirectory(prefix="3t-client-", dir=cache) as temporary_directory:
            manifest = Path(temporary_directory) / "workflow.yaml"
            _ = manifest.write_bytes(render_workflow_bytes(workload))
            output = run_client_validation(manifest, root)
    except (ClientValidationError, SchemaLockError) as error:
        typer.echo(f"CLIENT_INVALID {error}", err=True)
        raise typer.Exit(code=2) from error
    typer.echo(output, nl=False)


def main() -> None:
    """Run the platform command-line application."""
    app()
