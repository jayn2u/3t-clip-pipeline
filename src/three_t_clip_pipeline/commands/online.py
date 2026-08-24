"""Online operator command handlers registered on the root CLI."""

from __future__ import annotations

from pathlib import Path  # noqa: TC003 -- Typer resolves annotations at runtime.
from typing import Annotated

import typer

from three_t_clip_pipeline.clients import ClientExecutionError
from three_t_clip_pipeline.commands import (
    AuthorizationDeniedError,
    EnvironmentClass,
    ExitCode,
    Operation,
    authorize,
    dependencies,
)
from three_t_clip_pipeline.commands.context import ContextRequiredError, parse_context
from three_t_clip_pipeline.commands.loading import CommandInputError, render_workload_path
from three_t_clip_pipeline.commands.operations import is_successful_phase, write_evidence
from three_t_clip_pipeline.commands.operations import preflight as run_preflight
from three_t_clip_pipeline.commands.operations import status as read_status
from three_t_clip_pipeline.commands.operations import submit as run_submit
from three_t_clip_pipeline.commands.security import (  # noqa: TC001 -- Typer runtime metadata.
    ForbiddenSecretOption,
)


def preflight_command(
    *,
    context: Annotated[str, typer.Option(help="Explicit Kubernetes context.")],
    environment_class: Annotated[
        EnvironmentClass, typer.Option(help="Explicit environment trust class.")
    ],
    forbidden_secret: ForbiddenSecretOption = None,
) -> None:
    """Run authenticated read-only cluster inventory."""
    _ = forbidden_secret
    try:
        explicit_context = parse_context(context)
    except ContextRequiredError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=ExitCode.INPUT_INVALID) from error
    authorize(Operation.PREFLIGHT, environment_class)
    try:
        run_preflight(dependencies.kubernetes_client(explicit_context))
    except ClientExecutionError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=ExitCode.CLIENT_FAILED) from error
    typer.echo(f"READ_ONLY context={context} environment={environment_class.value}")


def submit_command(
    workload_path: Annotated[Path, typer.Argument(exists=False, dir_okay=False)],
    *,
    context: Annotated[str, typer.Option(help="Explicit Kubernetes context.")],
    environment_class: Annotated[
        EnvironmentClass, typer.Option(help="Explicit environment trust class.")
    ],
    watch: Annotated[bool, typer.Option(help="Wait for an authoritative terminal phase.")] = False,
    forbidden_secret: ForbiddenSecretOption = None,
) -> None:
    """Create the sole per-run Workflow mutation and optionally watch it."""
    _ = forbidden_secret
    try:
        explicit_context = parse_context(context)
    except ContextRequiredError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=ExitCode.INPUT_INVALID) from error
    try:
        authorize(Operation.SUBMIT, environment_class)
    except AuthorizationDeniedError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=ExitCode.AUTHORIZATION_DENIED) from error
    try:
        rendered = render_workload_path(workload_path)
        name, phase = run_submit(
            dependencies.kubernetes_client(explicit_context),
            rendered,
            watch=watch,
        )
    except CommandInputError as error:
        typer.echo(f"SUBMIT_INVALID {error}", err=True)
        raise typer.Exit(code=ExitCode.INPUT_INVALID) from error
    except ClientExecutionError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=ExitCode.CLIENT_FAILED) from error
    typer.echo(f"WORKFLOW_CREATED {name}")
    if phase is not None:
        if is_successful_phase(phase):
            typer.echo("WORKFLOW_SUCCEEDED")
            return
        typer.echo(f"WORKFLOW_TERMINAL phase={phase}", err=True)
        raise typer.Exit(code=ExitCode.WORKFLOW_FAILED)


def status_command(
    workflow_name: Annotated[str, typer.Argument()],
    *,
    context: Annotated[str, typer.Option(help="Explicit Kubernetes context.")],
    environment_class: Annotated[
        EnvironmentClass, typer.Option(help="Explicit environment trust class.")
    ],
    forbidden_secret: ForbiddenSecretOption = None,
) -> None:
    """Read one Workflow's authoritative phase."""
    _ = forbidden_secret
    try:
        explicit_context = parse_context(context)
    except ContextRequiredError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=ExitCode.INPUT_INVALID) from error
    authorize(Operation.STATUS, environment_class)
    try:
        phase = read_status(dependencies.kubernetes_client(explicit_context), workflow_name)
    except ClientExecutionError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=ExitCode.CLIENT_FAILED) from error
    typer.echo(f"WORKFLOW_PHASE {phase}")


def evidence_command(
    workflow_name: Annotated[str, typer.Argument()],
    *,
    context: Annotated[str, typer.Option(help="Explicit Kubernetes context.")],
    environment_class: Annotated[
        EnvironmentClass, typer.Option(help="Explicit environment trust class.")
    ],
    output: Annotated[Path, typer.Option(help="Sanitized JSON evidence output path.")],
    forbidden_secret: ForbiddenSecretOption = None,
) -> None:
    """Write permit-listed read-only evidence for one Workflow."""
    _ = forbidden_secret
    try:
        explicit_context = parse_context(context)
    except ContextRequiredError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=ExitCode.INPUT_INVALID) from error
    authorize(Operation.EVIDENCE, environment_class)
    try:
        write_evidence(dependencies.kubernetes_client(explicit_context), workflow_name, output)
    except ClientExecutionError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=ExitCode.CLIENT_FAILED) from error
    typer.echo(f"EVIDENCE_WRITTEN {output}")


def register_online_commands(app: typer.Typer) -> None:
    """Register online handlers without adding an extra command group."""
    _ = app.command("preflight")(preflight_command)
    _ = app.command("submit")(submit_command)
    _ = app.command("status")(status_command)
    _ = app.command("evidence")(evidence_command)
