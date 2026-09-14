FROM python:3.12.10-slim-bookworm

ARG ODOO_COMMIT=a2d73c5900d8886d115afe1ccb7f5c97c7e71a97
ARG VLUX_VERSION=0.0.0
ARG SOURCE_COMMIT=unknown
ARG BUILD_CREATED=unknown

LABEL org.opencontainers.image.title="VLUX POS"
LABEL org.opencontainers.image.version="${VLUX_VERSION}"
LABEL org.opencontainers.image.source="https://github.com/vluxaimkt/vlux-pos-odoo19"
LABEL org.opencontainers.image.revision="${SOURCE_COMMIT}"
LABEL org.opencontainers.image.created="${BUILD_CREATED}"
LABEL vlux.odoo.commit="${ODOO_COMMIT}"

ENV ODOO_HOME=/opt/vlux/pos/odoo \
    VLUX_POS_ROOT=/opt/vlux/pos \
    VLUX_POS_CONFIG=/etc/vlux-pos \
    VLUX_POS_DATA=/var/lib/vlux-pos \
    VLUX_POS_LOGS=/var/log/vlux-pos

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      ca-certificates curl git libpq5 libxml2 libxslt1.1 libsasl2-2 libldap-2.5-0 \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --system --home /var/lib/vlux-pos --create-home --shell /usr/sbin/nologin vlux-pos

WORKDIR /opt/vlux/pos
COPY requirements-extra.txt /opt/vlux/pos/requirements-extra.txt
COPY vlux_core /opt/vlux/pos/addons/vlux_core
COPY vlux_mobile_scanner /opt/vlux/pos/addons/vlux_mobile_scanner
COPY vlux_owner /opt/vlux/pos/addons/vlux_owner
COPY scripts/distribution /opt/vlux/pos/scripts/distribution

RUN git clone --depth 1 https://github.com/odoo/odoo.git "$ODOO_HOME" \
    && cd "$ODOO_HOME" \
    && git fetch --depth 1 origin "$ODOO_COMMIT" \
    && git checkout "$ODOO_COMMIT" \
    && pip install --no-cache-dir -r "$ODOO_HOME/requirements.txt" \
    && pip install --no-cache-dir -r /opt/vlux/pos/requirements-extra.txt \
    && install -d -o vlux-pos -g vlux-pos /etc/vlux-pos /var/lib/vlux-pos /var/log/vlux-pos

USER vlux-pos
EXPOSE 8069
CMD ["python", "/opt/vlux/pos/odoo/odoo-bin", "-c", "/etc/vlux-pos/odoo.conf"]
