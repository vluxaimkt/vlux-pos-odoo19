# VLUX POS — Cloudflare temporary staging

`DEPLOYMENT_CLASS=CLOUDFLARE_TEMPORARY_STAGING`

This mode runs the **same** VLUX Cloud Managed stack — same OCI image, same Odoo
commit, same addons, same tenant layout, same PostgreSQL major, same backup
format — on a machine you already own, published through a Cloudflare Tunnel.

It exists so a client can start working on real data before the final VPS is
ready, and so that tenant can later be moved to the VPS **without recapturing
inventory, sales, customers or configuration**. See
[MIGRATION_TO_VPS.md](MIGRATION_TO_VPS.md).

It is not a disposable demo and it is not production-grade infrastructure.

## What it gives you

* Public HTTPS on a real domain, terminated by Cloudflare.
* No public IP, no port forwarding, no DMZ, no router changes.
* No inbound firewall rule: `cloudflared` dials **out** to Cloudflare.
* Nothing published on the host at all — not 80, 443, 5432, 8069 or 8072.

```
Internet → Cloudflare edge (public TLS) → Tunnel (outbound only)
        → cloudflared container → vlux-edge Docker network
        → Caddy edge → tenant app → tenant PostgreSQL (private network)
```

`cloudflared` is attached to `vlux-edge` and nothing else, so it can reach the
Caddy edge and **cannot** reach any tenant database.

## What it does not give you

* **No Internet, no public access.** The tunnel needs an outbound connection.
  Odoo and PostgreSQL keep running locally and no data is lost, but the public
  hostname stops answering until connectivity returns. This is documented
  behaviour, not a failure to fix — there is no high availability here.
* **No datacenter SLA.** A host holding real client data needs, at minimum: a
  stable machine, Docker enabled at boot (`systemctl enable docker`), the VLUX
  stacks set to `restart: unless-stopped` (they are), the tunnel enabled at
  boot, and working off-site backups. Power loss is your risk to manage.

## Step 1 — what you do in the Cloudflare dashboard

Do this yourself. Never paste a token into a chat, a ticket or this repository.

1. Sign in to the Cloudflare dashboard and pick the account holding your domain.
2. Go to **Zero Trust → Networks → Tunnels**.
3. **Create a tunnel**, choose **Cloudflared**, and name it `vlux-pos-staging`.
4. Cloudflare shows an install command containing a long token. Copy **only the
   token value** (the long base64 string, not the whole command).
5. Still in the tunnel, open **Public Hostnames → Add a public hostname**:
   * **Subdomain / Domain**: the hostname the client will use, e.g.
     `pos-demo.example.com`.
   * **Service type**: `HTTP`
   * **URL**: `vlux-edge-caddy:80`
   * Under **Additional application settings → HTTP Settings**, leave
     `HTTP Host Header` empty so the original Host reaches Caddy. The tenant
     route is matched on that Host.
6. Save. Cloudflare creates the DNS record for you; it is proxied by default,
   which is what you want.

Use a **remotely managed** tunnel as above. A Quick Tunnel
(`cloudflared tunnel --url`) gives a throwaway `trycloudflare.com` hostname with
no stable name and no access control: acceptable to demonstrate the product,
never acceptable for real client data. `QUICK_TUNNEL=DEMO_ONLY`.

## Step 2 — install the token on the host

Write the token to a file yourself; nothing echoes it back.

```bash
sudo install -d -m 0700 /srv/vlux-pos/secrets
sudo install -m 0600 /dev/null /tmp/vlux-tunnel-token
sudo tee /tmp/vlux-tunnel-token >/dev/null   # paste the token, then Ctrl-D
```

## Step 3 — bring up the host in tunnel mode

```bash
sudo vlux-cloud host-init --edge-mode cloudflare_tunnel
sudo vlux-cloud staging-init --tunnel-token-file /tmp/vlux-tunnel-token
sudo shred -u /tmp/vlux-tunnel-token
```

`staging-init` refuses to run if the edge is publishing any host port, copies
the token to `/srv/vlux-pos/secrets/cloudflare_tunnel_token` with mode `0600`
owned by the `cloudflared` account, renders the connector stack, starts it and
waits for the tunnel to report a connection.

The token is passed to `cloudflared` with `--token-file` from a Docker secret,
so it never appears in `argv`, in the container environment, in
`docker inspect`, in logs, in `tenant.json` or in any CI artifact. Only its
length and a 12-character SHA-256 fingerprint are recorded.

## Step 4 — provision the tenant

```bash
sudo vlux-cloud provision cliente01 \
  --domain pos-demo.example.com \
  --owner-email owner@cliente.com \
  --data-class REAL_CLIENT_DATA \
  --image ghcr.io/vluxaimkt/vlux-pos@sha256:<digest>
```

`--data-class REAL_CLIENT_DATA` makes `vlux-cloud status` report
`BACKUP_PROTECTION=DEGRADED` whenever the tenant has no recent backup, no
off-site configuration, or a failed last off-site upload. It warns; it never
blocks the tenant.

In tunnel mode the tenant route is served as plain `http://<domain>` **inside**
the Docker network. Caddy requests no certificate because Cloudflare already
terminated the public TLS. Host-based routing and the `/websocket*` → gevent
`8072` split work exactly as in public mode, so the POS bus and the phone
scanner behave the same.

## Day-to-day

```bash
sudo vlux-cloud tunnel status     # connector state and ready connections
sudo vlux-cloud tunnel start
sudo vlux-cloud tunnel stop
sudo vlux-cloud status            # includes deployment class and tunnel state
sudo vlux-cloud health cliente01
```

Start the tunnel automatically with the host:

```bash
sudo systemctl enable docker
# the connector stack already uses restart: unless-stopped
```

## Verifying the public endpoint

CI cannot prove this — it has no Cloudflare account — so it stays
`CLOUDFLARE_PUBLIC_HTTPS=MANUAL_PENDING` until you check by hand:

```bash
curl -sS https://pos-demo.example.com/vlux/health
# expect: {"status": "ok"} with no TLS warning
```

## Security notes

* The tunnel token is a credential for your Cloudflare account. Treat it like a
  password: rotate it in the dashboard if it is ever exposed, then re-run
  `staging-init` with the new token.
* `cloudflared` runs unprivileged (uid 65532), read-only, with all capabilities
  dropped and `no-new-privileges`, at log level `info` with rotation.
* Ask Cloudflare for a **runtime tunnel token only**. A broad Cloudflare API
  token with DNS or Tunnel admin rights is a separate, administrative
  credential and this tool never needs it.
