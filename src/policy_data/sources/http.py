from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urljoin, urlparse

import httpx

from policy_data.sources.artifacts import ArtifactStore, StoredArtifact
from policy_data.sources.registry import SourceDefinition


class FetchRejected(RuntimeError):
    pass


Resolver = Callable[[str], list[str]]


def _resolve(host: str) -> list[str]:
    return sorted(
        {
            str(item[4][0])
            for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        }
    )


def _validate_addresses(host: str, resolver: Resolver) -> None:
    addresses = resolver(host)
    if not addresses:
        raise FetchRejected("source host resolved to no address")
    for value in addresses:
        address = ipaddress.ip_address(value)
        if not address.is_global:
            raise FetchRejected("source host resolved to a prohibited address")


class SafeFetcher:
    def __init__(
        self,
        store: ArtifactStore,
        *,
        transport: httpx.BaseTransport | None = None,
        resolver: Resolver = _resolve,
    ) -> None:
        self.store = store
        self.transport = transport
        self.resolver = resolver

    def fetch(self, source: SourceDefinition) -> StoredArtifact:
        prior = self.store.latest(source.source_id)
        headers = {"accept": ", ".join(sorted(source.media_types))}
        if prior and prior.etag:
            headers["if-none-match"] = prior.etag
        if prior and prior.last_modified:
            headers["if-modified-since"] = prior.last_modified

        url = source.url
        timeout = httpx.Timeout(30.0, connect=10.0)
        with httpx.Client(
            transport=self.transport, timeout=timeout, follow_redirects=False
        ) as client:
            for _ in range(4):
                parsed = urlparse(url)
                host = (parsed.hostname or "").lower()
                if parsed.scheme != "https" or host not in source.allowed_hosts:
                    raise FetchRejected(
                        "redirect target is outside the official allowlist"
                    )
                _validate_addresses(host, self.resolver)
                with client.stream("GET", url, headers=headers) as response:
                    if response.status_code == 304:
                        if prior is None:
                            raise FetchRejected(
                                "304 response has no verified prior artifact"
                            )
                        return prior
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise FetchRejected("redirect response omitted location")
                        url = urljoin(url, location)
                        continue
                    response.raise_for_status()
                    media_type = (
                        response.headers.get("content-type", "")
                        .split(";", 1)[0]
                        .strip()
                        .lower()
                    )
                    if (
                        media_type == "text/html"
                        and "text/html" not in source.media_types
                    ):
                        raise FetchRejected(
                            "HTML browser challenge is not an accepted artifact"
                        )
                    if media_type not in source.media_types:
                        raise FetchRejected(
                            f"unexpected media type: {media_type or 'missing'}"
                        )
                    chunks: list[bytes] = []
                    total = 0
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > source.max_bytes:
                            raise FetchRejected("source exceeded maximum byte count")
                        chunks.append(chunk)
                    body = b"".join(chunks)
                    if (
                        media_type == "text/html"
                        and b"challenge" in body[:8192].lower()
                    ):
                        raise FetchRejected("HTML browser challenge body rejected")
                    return self.store.persist(
                        source,
                        body,
                        media_type=media_type,
                        etag=response.headers.get("etag"),
                        last_modified=response.headers.get("last-modified"),
                    )
        raise FetchRejected("too many redirects")
