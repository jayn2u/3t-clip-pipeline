"""Hidden early rejection for prohibited secret-bearing CLI flags."""

from __future__ import annotations

from typing import Annotated, TypeAlias

import typer


def reject_secret_flag(value: str | None) -> None:
    """Reject and discard a supplied secret value before command execution."""
    if value is not None:
        typer.echo("secret_flag_forbidden", err=True)
        raise typer.Exit(code=3)


ForbiddenSecretOption: TypeAlias = Annotated[  # noqa: UP040 -- Typer needs runtime Annotated.
    str | None,
    typer.Option(
        "--secret",
        "--password",
        "--token",
        "--access-key",
        hidden=True,
        is_eager=True,
        callback=reject_secret_flag,
    ),
]
