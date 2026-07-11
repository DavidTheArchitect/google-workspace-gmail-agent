"""Redacted, integrity-independent export of selected text audit artifacts."""

import shutil
from pathlib import Path

from compliance_agent.audit.html import sanitize_html
from compliance_agent.audit.redaction import redact_text
from compliance_agent.exceptions import AuditWriteFailure

_TEXT_SUFFIXES = frozenset({".json", ".jsonl", ".md", ".txt", ".html", ".yaml", ".yml"})


def export_redacted(source_run: Path, destination: Path) -> Path:
    """Create a new shareable directory; never modify protected source evidence."""

    if not source_run.exists() or not source_run.is_dir() or source_run.is_symlink():
        message = f"protected audit source is not a regular directory: {source_run}"
        raise AuditWriteFailure(message)
    source = source_run.resolve()
    target = destination.resolve()
    if target.exists():
        message = f"redacted export destination already exists: {target}"
        raise AuditWriteFailure(message)
    if source in target.parents:
        message = "redacted export destination cannot be inside the protected source"
        raise AuditWriteFailure(message)
    try:
        target.mkdir(mode=0o700, parents=True)
        target.chmod(0o700)
        for path in sorted(source.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(source)
            exported = target / relative
            exported.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            exported.parent.chmod(0o700)
            if path.suffix.lower() in _TEXT_SUFFIXES:
                content = path.read_text(encoding="utf-8")
                safe_content = (
                    sanitize_html(content)
                    if path.suffix.lower() == ".html"
                    else redact_text(content)
                )
                exported.write_text(safe_content, encoding="utf-8")
                exported.chmod(0o600)
    except (OSError, UnicodeError) as error:
        shutil.rmtree(target, ignore_errors=True)
        message = f"redacted export failed for {source}"
        raise AuditWriteFailure(message) from error
    return target
