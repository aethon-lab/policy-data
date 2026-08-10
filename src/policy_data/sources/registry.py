from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode, urlparse


@dataclass(frozen=True, slots=True)
class SourceDefinition:
    source_id: str
    publisher: str
    dataset: str
    legislature: int
    chamber: str
    url: str
    allowed_hosts: frozenset[str]
    media_types: frozenset[str]
    max_bytes: int
    license_id: str
    adapter_version: str
    role: str | None = None
    archive_member: str | None = None
    query: str | None = None

    def __post_init__(self) -> None:
        host = (urlparse(self.url).hostname or "").lower()
        if urlparse(self.url).scheme != "https":
            raise ValueError(f"source {self.source_id}: HTTPS is required")
        if host not in self.allowed_hosts:
            raise ValueError(f"source {self.source_id}: URL host is outside allowlist")
        if self.chamber not in {"camera", "senato"}:
            raise ValueError(f"source {self.source_id}: unsupported chamber")
        if self.legislature <= 0 or self.max_bytes <= 0:
            raise ValueError(f"source {self.source_id}: invalid numeric bound")
        if not self.media_types:
            raise ValueError(f"source {self.source_id}: media types are required")

    @property
    def request_url(self) -> str:
        if self.query is None:
            return self.url
        separator = "&" if "?" in self.url else "?"
        return (
            self.url
            + separator
            + urlencode(
                {
                    "query": self.query,
                    "output": "application/sparql-results+json",
                }
            )
        )


class SourceRegistry:
    def __init__(self, sources: list[SourceDefinition]) -> None:
        self._sources = {source.source_id: source for source in sources}
        if len(self._sources) != len(sources):
            raise ValueError("source IDs must be unique")

    @classmethod
    def load(cls, path: Path) -> "SourceRegistry":
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        sources = []
        for item in raw.get("sources", []):
            windows = item.get("date_windows") or (None,)
            for window in windows:
                query = item.get("query")
                source_id = item["id"]
                if window is not None:
                    if not isinstance(window, list) or len(window) != 2 or not query:
                        raise ValueError(f"source {source_id}: invalid date window")
                    start, end = window
                    query = query.replace("__START__", start).replace("__END__", end)
                    source_id = f"{source_id}-{start}-{end}"
                sources.append(
                    SourceDefinition(
                        source_id=source_id,
                        publisher=item["publisher"],
                        dataset=item["dataset"],
                        legislature=item["legislature"],
                        chamber=item["chamber"],
                        url=item["url"],
                        allowed_hosts=frozenset(
                            host.lower() for host in item["allowed_hosts"]
                        ),
                        media_types=frozenset(item["media_types"]),
                        max_bytes=item["max_bytes"],
                        license_id=item["license"],
                        adapter_version=item["adapter_version"],
                        role=item.get("role"),
                        archive_member=item.get("archive_member"),
                        query=query,
                    )
                )
        if not sources:
            raise ValueError("source registry is empty")
        return cls(sources)

    def require(self, source_id: str) -> SourceDefinition:
        try:
            return self._sources[source_id]
        except KeyError as error:
            raise KeyError(f"unknown source: {source_id}") from error

    def all(self) -> tuple[SourceDefinition, ...]:
        return tuple(self._sources.values())
