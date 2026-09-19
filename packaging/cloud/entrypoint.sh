#!/bin/sh
# VLUX POS cloud entrypoint.
#
# Runs odoo-bin against the tenant configuration mounted at
# ${VLUX_POS_CONFIG}/odoo.conf and forwards every argument, so the provisioning
# CLI can call the image directly:
#
#   docker compose run --rm app -d <db> -i <modules> --stop-after-init
#   docker compose run --rm app shell -d <db> --no-http
#
# odoo-bin only recognises a subcommand when it is the FIRST argument, so a
# leading non-option argument is moved ahead of -c.
set -eu

CONFIG="${VLUX_POS_CONFIG:-/etc/vlux-pos}/odoo.conf"
PYTHON="${VLUX_VENV:-/opt/vlux/pos/venv}/bin/python"
ODOO_BIN="${ODOO_HOME:-/opt/vlux/pos/odoo}/odoo-bin"

if [ ! -r "$CONFIG" ]; then
    echo "vlux-entrypoint: tenant configuration not readable at $CONFIG" >&2
    exit 78
fi

if [ "$#" -gt 0 ] && [ "${1#-}" = "$1" ]; then
    command="$1"
    shift
    exec "$PYTHON" "$ODOO_BIN" "$command" -c "$CONFIG" "$@"
fi

exec "$PYTHON" "$ODOO_BIN" -c "$CONFIG" "$@"
