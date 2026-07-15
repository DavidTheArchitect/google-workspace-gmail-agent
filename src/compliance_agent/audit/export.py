"""Redacted, integrity-independent export of selected text audit artifacts."""

import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path

from compliance_agent.audit.html import sanitize_html
from compliance_agent.audit.redaction import redact_text
from compliance_agent.exceptions import AuditWriteFailure
from compliance_agent.infrastructure.permissions import restrict_permissions

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
        restrict_permissions(target, 0o700)
        for path in sorted(source.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(source)
            exported = target / relative
            exported.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            restrict_permissions(exported.parent, 0o700)
            if path.suffix.lower() in _TEXT_SUFFIXES:
                content = path.read_text(encoding="utf-8")
                safe_content = (
                    sanitize_html(content)
                    if path.suffix.lower() == ".html"
                    else redact_text(content)
                )
                exported.write_text(safe_content, encoding="utf-8")
                restrict_permissions(exported, 0o600)
    except (OSError, UnicodeError) as error:
        shutil.rmtree(target, ignore_errors=True)
        message = f"redacted export failed for {source}"
        raise AuditWriteFailure(message) from error
    return target


def export_redacted_zip(source_run: Path, destination: Path) -> Path:
    """Create a deterministic shareable ZIP with its own redacted-export manifest."""

    target = destination.resolve()
    if target.exists():
        message = f"redacted ZIP destination already exists: {target}"
        raise AuditWriteFailure(message)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary_zip: Path | None = None
    try:
        with tempfile.TemporaryDirectory(dir=target.parent, prefix=".redacted-export-") as name:
            export_root = export_redacted(source_run, Path(name) / "package")
            _write_export_manifest(export_root)
            descriptor, temporary_name = tempfile.mkstemp(
                dir=target.parent,
                prefix=f".{target.name}.",
            )
            temporary_zip = Path(temporary_name)
            os.close(descriptor)
            temporary_zip.unlink()
            _write_deterministic_zip(export_root, temporary_zip)
            temporary_zip.replace(target)
            restrict_permissions(target, 0o600)
    except (OSError, UnicodeError, zipfile.BadZipFile) as error:
        if temporary_zip is not None:
            temporary_zip.unlink(missing_ok=True)
        message = f"redacted ZIP export failed for {source_run}"
        raise AuditWriteFailure(message) from error
    return target


def _write_export_manifest(root: Path) -> None:
    artifacts = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        content = path.read_bytes()
        artifacts.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
            }
        )
    manifest = {"schema_version": "1.0", "artifacts": artifacts}
    path = root / "export-manifest.json"
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    restrict_permissions(path, 0o600)


def _write_deterministic_zip(root: Path, destination: Path) -> None:
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            info = zipfile.ZipInfo(path.relative_to(root).as_posix(), (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, path.read_bytes())
