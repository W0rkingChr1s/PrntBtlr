# Contributing to PrntBtlr

Thanks for your interest in improving PrntBtlr! Contributions of all kinds are
welcome — bug reports, fixes, features, docs, and hardware test reports.

## Ground rules

- Be kind. This project follows the [Code of Conduct](CODE_OF_CONDUCT.md).
- For anything non-trivial, open an issue first so we can agree on the approach.
- Keep PRs focused: one logical change per pull request.

## Development setup

```bash
git clone https://github.com/w0rkingchr1s/prntbtlr.git
cd prntbtlr
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
```

Run the panel locally (no printer/scanner needed — it degrades gracefully):

```bash
PRNTBTLR_PORT=8080 PRNTBTLR_DEBUG=1 python -m app.main   # http://localhost:8080
```

## Before you push

The CI runs these — please run them locally too:

```bash
pytest                      # tests must pass
ruff check .                # lint
ruff format .               # auto-format (CI checks with --check)
shellcheck scripts/*.sh     # shell scripts
```

## Project layout

| Path | What lives here |
|------|-----------------|
| `app/services/` | Thin wrappers around CUPS/SANE/systemd (the logic). |
| `app/routes/` | FastAPI routers (HTTP + form handling). |
| `app/templates/` | Jinja2 server-rendered pages + partials. |
| `app/static/` | CSS / JS / icons (no build step, no CDN). |
| `scripts/` | Installer, uninstaller, and the scanbd handler. |
| `deploy/` | systemd unit, Dockerfile, compose. |
| `tests/` | pytest suite (parsers, guards, page smoke tests). |

## Coding conventions

- **Shell out via argv lists**, never `shell=True` — printer/scanner names are
  user input and must not reach a shell. Use `app/services/shell.py`.
- Add a test when you touch a parser or add a route.
- Keep the UI dependency-free (vanilla JS + CSS); don't add a frontend build step.
- Match the surrounding style; `ruff format` is the source of truth.

## Commit & PR

- Write clear, present-tense commit messages ("Add ADF duplex option").
- Fill in the PR template and link the issue (`Closes #123`).
- Green CI is required before merge.

## Reporting bugs / requesting features

Use the [issue templates](https://github.com/w0rkingchr1s/prntbtlr/issues/new/choose).
For security issues, **do not open a public issue** — see [SECURITY.md](SECURITY.md).
