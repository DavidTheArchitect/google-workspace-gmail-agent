"""No-argument entry point for the local operator console."""

from compliance_agent.cli import run


def main() -> None:
    """Start the console with its secure automatic browser handoff."""

    raise SystemExit(run(["console"]))
