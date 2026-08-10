from pathlib import Path

import pytest

from policy_data.sources.registry import SourceRegistry


def test_registry_loads_both_chambers_and_source_licenses() -> None:
    registry = SourceRegistry.load(Path("config/sources.toml"))

    camera = registry.require("camera_votes_xix")
    senato = registry.require("senato_votes_xix")
    assert camera.legislature == 19 and camera.chamber == "camera"
    assert camera.license_id == "CC-BY-SA-4.0"
    assert senato.license_id == "CC-BY-3.0"
    assert camera.allowed_hosts == frozenset({"dati.camera.it"})


def test_registry_rejects_a_canonical_url_outside_allowlist(tmp_path: Path) -> None:
    path = tmp_path / "sources.toml"
    path.write_text(
        """[[sources]]
id='bad'
publisher='x'
dataset='x'
legislature=19
chamber='camera'
url='https://evil.example/data'
allowed_hosts=['dati.camera.it']
media_types=['application/zip']
max_bytes=100
license='CC-BY-SA-4.0'
adapter_version='1'
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="allowlist"):
        SourceRegistry.load(path)
