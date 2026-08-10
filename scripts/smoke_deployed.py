from __future__ import annotations

import argparse
import os

import httpx


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    key = os.getenv("POLICY_DATA_API_KEY")
    with httpx.Client(base_url=base, timeout=15, follow_redirects=False) as client:
        for path in (
            "/",
            "/health",
            "/openapi.json",
            "/llms.txt",
            "/robots.txt",
            "/sitemap.xml",
        ):
            response = client.get(path)
            response.raise_for_status()
        unauthenticated = client.get("/api/v1/voters", params={"limit": 1})
        if unauthenticated.status_code != 401:
            raise SystemExit("REST endpoint did not reject a missing API key")
        if key:
            response = client.get(
                "/api/v1/voters",
                params={"limit": 1},
                headers={"Authorization": f"Bearer {key}"},
            )
            response.raise_for_status()
    print(f"smoke checks passed for {base}")


if __name__ == "__main__":
    main()
