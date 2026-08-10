from __future__ import annotations

import os
from pathlib import Path

from policy_data.ingest.publish import read_active_release
from policy_data.ingest.manifest import parse_manifest
from policy_data.ingest.validate import validate_release_directory


def main() -> None:
    root = Path(os.getenv("POLICY_DATA_DATA_DIR", "data")) / "published"
    release_id = read_active_release(root)
    if release_id is None:
        raise SystemExit(
            "no active release to validate; build a source-backed release before activation"
        )
    release_root = root / "releases" / release_id
    manifest = parse_manifest((release_root / "manifest.json").read_bytes())
    validate_release_directory(release_root, manifest)
    print(f"validated active release {release_id}")


if __name__ == "__main__":
    main()
