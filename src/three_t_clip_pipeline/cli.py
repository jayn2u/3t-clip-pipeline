"""Command-line entry point for the platform package."""

from typing import Annotated

import typer

from three_t_clip_pipeline import __version__

app = typer.Typer(
    name="3t-pipeline",
    help="Build and operate portable 3T-CLIP pipeline workloads.",
    no_args_is_help=True,
)


@app.command()
def version(
    *,
    short: Annotated[bool, typer.Option(help="Print only the version number.")] = False,
) -> None:
    """Show the installed platform version."""
    typer.echo(__version__ if short else f"3t-pipeline {__version__}")


def main() -> None:
    """Run the platform command-line application."""
    app()
