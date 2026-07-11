"""Print fixture hashes and sizes without rendering or executing fixture content."""

import argparse
import hashlib
from pathlib import Path


def main() -> None:
    """Inspect one sanitized fixture as inert bytes."""

    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    path = parser.parse_args().path
    content = path.read_bytes()
    print(f"path={path}")
    print(f"size_bytes={len(content)}")
    print(f"sha256={hashlib.sha256(content).hexdigest()}")


if __name__ == "__main__":
    main()
