# Ubuntu Distribution

Target:

- Ubuntu 24.04 LTS x86_64
- `vlux-pos_<version>_amd64.deb`
- Install command: `sudo apt install ./vlux-pos_<version>_amd64.deb`

Filesystem layout:

- `/opt/vlux/pos/`
- `/etc/vlux-pos/`
- `/var/lib/vlux-pos/`
- `/var/lib/vlux-pos/filestore/`
- `/var/backups/vlux-pos/`
- `/var/log/vlux-pos/`

After package installation, run:

```sh
sudo vlux-pos setup --business-name "Cliente" --edition local_complete --hostname pos.example.com
```

Manual validation pending:

- Clean-machine install.
- Public-domain HTTPS.
- Trusted local CA for LAN/offline installs.
- Physical phone camera scanner flow.
