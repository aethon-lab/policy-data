ARG PYTHON_IMAGE=python:3.13.14-slim-bookworm@sha256:67a1e1f215ccda113cfc024e8639049257e88f273898f595b61476d128d387e8
FROM ${PYTHON_IMAGE} AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN python -m pip wheel --wheel-dir /wheels .

FROM ${PYTHON_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH
RUN groupadd --gid 10001 policy-data \
    && useradd --uid 10001 --gid 10001 --home-dir /nonexistent --no-create-home policy-data \
    && mkdir -p /var/lib/policy-data \
    && chown 10001:10001 /var/lib/policy-data \
    && python -m venv /opt/venv
COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/* \
    && rm -rf /wheels

WORKDIR /app
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "policy_data.runtime:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-proxy-headers", "--access-log"]
