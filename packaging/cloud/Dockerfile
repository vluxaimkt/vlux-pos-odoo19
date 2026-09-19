# VLUX POS Cloud Managed runtime image.
#
# Stage 1 (builder) carries compilers and development headers so that the Odoo
# and VLUX Python dependencies can be built. Stage 2 (runtime) receives only the
# resulting virtualenv, the pinned Odoo tree and the productive VLUX addons, so
# no build tooling ships to production hosts.

ARG PYTHON_BASE=python:3.12.10-slim-bookworm@sha256:fd95fa221297a88e1cf49c55ec1828edd7c5a428187e67b5d1805692d11588db

FROM ${PYTHON_BASE} AS builder

ARG ODOO_COMMIT=a2d73c5900d8886d115afe1ccb7f5c97c7e71a97

ENV ODOO_HOME=/opt/vlux/pos/odoo \
    VLUX_VENV=/opt/vlux/pos/venv \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      build-essential \
      ca-certificates \
      git \
      libldap2-dev \
      libpq-dev \
      libsasl2-dev \
      libxml2-dev \
      libxslt1-dev \
      zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

# Pinned Odoo tree. The .git directory is dropped so the runtime image cannot be
# mutated with `git pull` and carries no upstream history.
RUN git init "$ODOO_HOME" \
    && git -C "$ODOO_HOME" remote add origin https://github.com/odoo/odoo.git \
    && git -C "$ODOO_HOME" fetch --depth 1 origin "$ODOO_COMMIT" \
    && git -C "$ODOO_HOME" checkout --detach FETCH_HEAD \
    && test "$(git -C "$ODOO_HOME" rev-parse HEAD)" = "$ODOO_COMMIT" \
    && printf '%s\n' "$ODOO_COMMIT" > /opt/vlux/pos/odoo-commit.txt \
    && rm -rf "$ODOO_HOME/.git"

COPY requirements-extra.txt /opt/vlux/pos/requirements-extra.txt

RUN python -m venv "$VLUX_VENV" \
    && "$VLUX_VENV/bin/pip" install "pip==24.3.1" "setuptools==75.8.0" "wheel==0.45.1" \
    && "$VLUX_VENV/bin/pip" install -r "$ODOO_HOME/requirements.txt" \
    && "$VLUX_VENV/bin/pip" install -r /opt/vlux/pos/requirements-extra.txt \
    && "$VLUX_VENV/bin/python" -m pip check


FROM ${PYTHON_BASE} AS runtime

ARG ODOO_COMMIT=a2d73c5900d8886d115afe1ccb7f5c97c7e71a97
ARG VLUX_VERSION=0.0.0
ARG SOURCE_COMMIT=unknown
ARG BUILD_CREATED=unknown
ARG VLUX_EDITION=cloud_managed
ARG VLUX_UID=10001
ARG VLUX_GID=10001

LABEL org.opencontainers.image.title="VLUX POS"
LABEL org.opencontainers.image.description="VLUX POS cloud managed Odoo 19 runtime"
LABEL org.opencontainers.image.version="${VLUX_VERSION}"
LABEL org.opencontainers.image.source="https://github.com/vluxaimkt/vlux-pos-odoo19"
LABEL org.opencontainers.image.revision="${SOURCE_COMMIT}"
LABEL org.opencontainers.image.created="${BUILD_CREATED}"
LABEL org.opencontainers.image.vendor="VLUX"
LABEL org.opencontainers.image.licenses="LGPL-3.0-or-later"
LABEL vlux.odoo.commit="${ODOO_COMMIT}"
LABEL vlux.python.version="3.12.10"
LABEL vlux.edition="${VLUX_EDITION}"
LABEL vlux.build.created="${BUILD_CREATED}"
LABEL vlux.source.commit="${SOURCE_COMMIT}"

ENV ODOO_HOME=/opt/vlux/pos/odoo \
    VLUX_VENV=/opt/vlux/pos/venv \
    VLUX_POS_ROOT=/opt/vlux/pos \
    VLUX_POS_CONFIG=/etc/vlux-pos \
    VLUX_POS_DATA=/var/lib/vlux-pos \
    VLUX_POS_LOGS=/var/log/vlux-pos \
    VLUX_HEALTH_URL=http://127.0.0.1:8069/vlux/health \
    PATH=/opt/vlux/pos/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/var/lib/vlux-pos

# Runtime shared objects only. No compiler, no -dev headers, no git.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      libldap-2.5-0 \
      libpq5 \
      libsasl2-2 \
      libxml2 \
      libxslt1.1 \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid "${VLUX_GID}" vlux-pos \
    && useradd --system --uid "${VLUX_UID}" --gid "${VLUX_GID}" \
        --home-dir /var/lib/vlux-pos --shell /usr/sbin/nologin vlux-pos

COPY --from=builder /opt/vlux/pos/venv /opt/vlux/pos/venv
COPY --from=builder /opt/vlux/pos/odoo /opt/vlux/pos/odoo
COPY --from=builder /opt/vlux/pos/odoo-commit.txt /opt/vlux/pos/odoo-commit.txt

COPY vlux_core /opt/vlux/pos/addons/vlux_core
COPY vlux_mobile_scanner /opt/vlux/pos/addons/vlux_mobile_scanner
COPY vlux_owner /opt/vlux/pos/addons/vlux_owner
COPY vlux_pos_catalog /opt/vlux/pos/addons/vlux_pos_catalog
COPY packaging/cloud/entrypoint.sh /usr/local/bin/vlux-entrypoint

RUN chmod 0755 /usr/local/bin/vlux-entrypoint \
    && install -d -m 0750 -o vlux-pos -g vlux-pos \
        /etc/vlux-pos /var/lib/vlux-pos /var/log/vlux-pos \
    && test ! -e /opt/vlux/pos/odoo/.git

USER vlux-pos
WORKDIR /opt/vlux/pos

EXPOSE 8069 8072

HEALTHCHECK --interval=20s --timeout=5s --start-period=90s --retries=6 \
    CMD curl -fsS "$VLUX_HEALTH_URL" | grep -q '"status": "ok"'

ENTRYPOINT ["/usr/local/bin/vlux-entrypoint"]
CMD []
