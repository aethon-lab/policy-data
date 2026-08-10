from pathlib import Path

import pytest

from policy_data.sources.registry import SourceRegistry


def test_registry_loads_both_chambers_and_source_licenses() -> None:
    registry = SourceRegistry.load(Path("config/sources.toml"))

    camera = registry.require("camera_votes_xix")
    senato = next(
        source for source in registry.all() if source.role == "senato_vote_window"
    )
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


def test_registry_expands_sparql_date_windows(tmp_path: Path) -> None:
    path = tmp_path / "sources.toml"
    path.write_text(
        """[[sources]]
id='votes'
publisher='x'
dataset='x'
legislature=19
chamber='senato'
url='https://dati.senato.it/sparql'
allowed_hosts=['dati.senato.it']
media_types=['application/sparql-results+json']
max_bytes=100
license='CC-BY-3.0'
adapter_version='1'
query='SELECT * WHERE { ?s ?p ?o } FILTER("__START__" < "__END__")'
date_windows=[['2022-01-01','2022-12-31'],['2023-01-01','2023-12-31']]
""",
        encoding="utf-8",
    )
    sources = SourceRegistry.load(path).all()
    assert len(sources) == 2
    assert sources[0].source_id.endswith("2022-01-01-2022-12-31")
    assert "2022-01-01" in sources[0].request_url
