# VLUX POS Distribution Scripts

`vlux_pos.py` is the shared administrative CLI used by Debian packaging and
cloud provisioning recipes. It intentionally keeps secrets out of stdout and
stores generated runtime credentials under the target platform config directory.

Core commands:

- `vlux-pos setup`
- `vlux-pos smoke`
- `vlux-pos backup`
- `vlux-pos restore <backup> --confirm RESTORE_VLUX_POS`

Physical install, HTTPS trust on phones, and real camera scans remain manual
validation gates.
