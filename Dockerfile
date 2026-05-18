# syntax=docker/dockerfile:1.7
# CAESAR runtime image (ADR-0029).
#
# Two-stage build: stage 1 installs the project into a venv with all
# default extras (LLM SDKs, calendar, etc.); stage 2 copies the venv
# into python:3.11-slim. Final image targets ~250MB. Multi-arch is
# handled by GitHub Actions buildx + QEMU.

# ---- builder ---------------------------------------------------------------
FROM python:3.11-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_ROOT_USER_ACTION=ignore

# libxml2 / libxslt are caldav transitive deps; gcc is needed by a few
# wheels for arm64 where prebuilt wheels are missing.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libxml2-dev \
        libxslt-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Copy only the project metadata first so dependency installs cache
# across source-only edits. Hatchling needs the package skeleton to
# resolve the project name; copy that in too.
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY caesar/ ./caesar/

# Build into a venv that the runtime image copies verbatim. PEP 668's
# externally-managed marker is a no-op inside the venv.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --upgrade pip \
    && pip install .

# ---- runtime ---------------------------------------------------------------
FROM python:3.11-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    CAESAR_SERVER__HOST=0.0.0.0

# libxml2 / libxslt runtime libs for caldav. No build toolchain.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libxml2 \
        libxslt1.1 \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

# Non-root user for the runtime. UID 10001 is well above typical host
# UIDs so volume-mount ownership doesn't conflict with the operator's
# user account. State lives in /var/lib/caesar (mountable, owned by
# the caesar user).
RUN groupadd --system --gid 10001 caesar \
    && useradd --system --uid 10001 --gid caesar --home /var/lib/caesar --shell /usr/sbin/nologin caesar \
    && mkdir -p /var/lib/caesar/var \
    && chown -R caesar:caesar /var/lib/caesar

USER caesar
WORKDIR /var/lib/caesar

EXPOSE 8000

# Healthcheck hits /healthz on loopback inside the container. Operator
# can override via `docker run --health-cmd=...`.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request, sys; \
        sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).status == 200 else 1)"

ENTRYPOINT ["caesar"]
CMD ["praetor", "serve"]
