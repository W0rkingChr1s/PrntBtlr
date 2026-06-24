# Security Policy

## Supported versions

PrntBtlr is pre-1.0. Security fixes are applied to the latest `main` and the
most recent release.

| Version | Supported |
|---------|-----------|
| latest `main` | ✅ |
| older tags    | ❌ |

## Reporting a vulnerability

**Please do not open a public issue for security problems.**

Report privately via GitHub's
[security advisories](https://github.com/w0rkingchr1s/prntbtlr/security/advisories/new).
If that's unavailable, email the maintainer at the address on their GitHub
profile. We aim to acknowledge reports within a few days.

Please include:

- A description of the issue and its impact.
- Steps to reproduce or a proof of concept.
- Affected version / commit.

## Security model & hardening notes

PrntBtlr is designed for a **trusted home LAN**, not direct internet exposure.
Be aware of the following by design:

- **No authentication** on the web panel. Anyone who can reach port 80 can manage
  printers and scans. Put it behind a reverse proxy with auth, a VPN, or a
  firewall rule before exposing it beyond your LAN.
- **Runs as root** (systemd unit) because it drives CUPS, SANE and systemd, which
  need privileges. It only shells out via argument lists (never a shell), so
  printer/scanner names can't trigger shell injection.
- **Samba share** defaults to guest access for convenience. For a locked-down
  share use `guest ok = no` + `smbpasswd`.
- **Saved-scan downloads** are guarded against path traversal and restricted to
  `*.pdf` inside the configured scan directory.

If you deploy PrntBtlr publicly, add authentication and TLS in front of it.
