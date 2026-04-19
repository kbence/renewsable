"""Placeholder Click CLI group.

Real subcommands (build, upload, run, install-schedule, uninstall-schedule,
pair, test-pipeline) are added in task 3.1. For task 1.1 this placeholder
exists solely so `renewsable --help` prints a sensible Click usage line,
satisfying the task's observable.
"""

import click


@click.group()
def main() -> None:
    """renewsable — daily news digest for the reMarkable 2."""
