"""Redacted, integrity-independent export of selected text audit artifacts."""

import shutil
from pathlib import Path

from compliance_agent.audit.redaction import redact_text
from compliance_agent.exceptions import AuditWriteFailure

_TEXT_SUFFIXES = frozenset({".json", ".jsonl", ".md", ".txt", ".html", ".yaml", ".yml"})


def export_redacted(source_run: Path, destination: Path) -> Path:
    """Create a new shareable directory; never modify protected source evidence."""

    source = source_run.resolve()
    target = destination.resolve()
    if target.exists():
        message = f"redacted export destination already exists: {target}"
        raise AuditWriteFailure(message)
    target.mkdir(mode=0o700, parents=True)
    try:
        for path in sorted(source.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(source)
            exported = target / relative
            exported.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if path.suffix.lower() in _TEXT_SUFFIXES:
                exported.write_text(redact_text(path.read_text(encoding="utf-8")), encoding="utf-8")
    except (OSError, UnicodeError) as error:
        shutil.rmtree(target, ignore_errors=True)
        message = f"redacted export failed for {source}"
        raise AuditWriteFailure(message) from error
    return target
