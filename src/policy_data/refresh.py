from __future__ import annotations

import argparse
import os
from pathlib import Path

from policy_data.ingest.manifest import parse_manifest
from policy_data.ingest.orchestrate import OfficialRefresh
from policy_data.ingest.pipeline import ReleaseBuilder
from policy_data.ingest.publish import read_active_release
from policy_data.ingest.validate import validate_release_directory
from policy_data.sources.artifacts import ArtifactStore
from policy_data.sources.http import SafeFetcher
from policy_data.sources.registry import SourceRegistry


def validate_active(root: Path) -> None:
    release_id = read_active_release(root)
    if release_id is None:
        raise SystemExit(
            "no active release to validate; build a source-backed release before activation"
        )
    release_root = root / "releases" / release_id
    manifest = parse_manifest((release_root / "manifest.json").read_bytes())
    validate_release_directory(release_root, manifest)
    print(f"validated active release {release_id}")


def build_release(root: Path, registry_path: Path, artifact_root: Path) -> None:
    result = OfficialRefresh(
        SourceRegistry.load(registry_path),
        SafeFetcher(ArtifactStore(artifact_root)),
        ReleaseBuilder(root),
    ).run()
    action = "created and activated" if result.created else "reactivated"
    print(f"{action} official release {result.release_id}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Policy Data release maintenance")
    parser.add_argument("command", choices=("build", "validate"))
    parser.add_argument("--registry", type=Path, default=Path("config/sources.toml"))
    args = parser.parse_args()
    data_root = Path(os.getenv("POLICY_DATA_DATA_DIR", "data"))
    published_root = data_root / "published"
    if args.command == "validate":
        validate_active(published_root)
    else:
        build_release(published_root, args.registry, data_root / "raw")


if __name__ == "__main__":
    main()
