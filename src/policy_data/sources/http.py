from __future__ import annotations

import hashlib
import html
import ipaddress
import re
import socket
from collections.abc import Callable, Iterable
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

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
        headers = {
            "accept": ", ".join(sorted(source.media_types)),
            "user-agent": "PolicyDataItalia/0.1 (official-source refresh)",
        }
        if prior and prior.etag:
            headers["if-none-match"] = prior.etag
        if prior and prior.last_modified:
            headers["if-modified-since"] = prior.last_modified

        url = source.request_url
        timeout = httpx.Timeout(120.0, connect=10.0)
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
                        challenge_body = b"".join(
                            self._bounded_chunks(response.iter_bytes(), 16_384)
                        )
                        challenge_url = self._camera_challenge_url(url, challenge_body)
                        if challenge_url is not None:
                            url = challenge_url
                            headers.pop("if-none-match", None)
                            headers.pop("if-modified-since", None)
                            continue
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
                    try:
                        return self.store.persist_stream(
                            source,
                            self._checked_chunks(response.iter_bytes(), media_type),
                            max_bytes=source.max_bytes,
                            media_type=media_type,
                            etag=response.headers.get("etag"),
                            last_modified=response.headers.get("last-modified"),
                        )
                    except ValueError as error:
                        if "maximum byte count" in str(error):
                            raise FetchRejected(str(error)) from error
                        raise
        raise FetchRejected("too many redirects")

    @staticmethod
    def _bounded_chunks(chunks: Iterable[bytes], maximum: int) -> Iterable[bytes]:
        total = 0
        for chunk in chunks:
            total += len(chunk)
            if total > maximum:
                raise FetchRejected("HTML challenge exceeded byte bound")
            yield chunk

    @staticmethod
    def _camera_challenge_url(url: str, body: bytes) -> str | None:
        text = body.decode("utf-8", errors="strict")
        if "js-challenge-form" not in text:
            return None
        action_match = re.search(
            r'<form id="js-challenge-form" action="([^"]+)" method="get">', text
        )
        hint_match = re.search(r'name="hint" value="([0-9a-f]{32,4096})"', text)
        x_match = re.search(r"var x\s*=\s*(\d{1,12})\s*;", text)
        digest_match = re.search(r'var y\s*=\s*"([0-9a-f]{40})"\s*;', text)
        if (
            action_match is None
            or hint_match is None
            or x_match is None
            or digest_match is None
        ):
            raise FetchRejected("unknown Camera browser challenge shape")
        parsed = urlparse(url)
        action = urlparse(urljoin(url, html.unescape(action_match.group(1))))
        if (
            action.scheme != parsed.scheme
            or action.hostname != parsed.hostname
            or action.path != parsed.path
        ):
            raise FetchRejected("Camera challenge action changed source path")
        start = int(x_match.group(1))
        expected = digest_match.group(1)
        answer = next(
            (
                candidate
                for candidate in range(100)
                if hashlib.sha1(str(start + candidate).encode()).hexdigest() == expected
            ),
            None,
        )
        if answer is None:
            raise FetchRejected("Camera browser challenge proof is unsatisfied")
        query = parse_qsl(action.query, keep_blank_values=True)
        query.extend((("hint", hint_match.group(1)), ("answer", str(answer))))
        return urlunparse(action._replace(query=urlencode(query)))

    @staticmethod
    def _checked_chunks(chunks: Iterable[bytes], media_type: str) -> Iterable[bytes]:
        prefix = bytearray()
        checked = media_type != "text/html"
        for chunk in chunks:
            if not checked:
                remaining = 8192 - len(prefix)
                prefix.extend(chunk[:remaining])
                if len(prefix) == 8192:
                    checked = True
                    if b"challenge" in prefix.lower():
                        raise FetchRejected("HTML browser challenge body rejected")
            yield chunk
        if not checked and b"challenge" in prefix.lower():
            raise FetchRejected("HTML browser challenge body rejected")
