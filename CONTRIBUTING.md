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

## Releasing

Releases are tag-driven, and **GitHub Releases are the only distribution
channel** — the panel's built-in updater feeds from them. There are two
channels:

### Beta releases

Cut one whenever there is something to test — **no CLI needed**: go to
**Actions → Cut release → Run workflow**, keep channel `beta`, and run it. The
workflow works out the next tag by itself (continues an open beta series with
`-beta.N+1`, otherwise bumps the last release — minor by default — or takes an
explicit `version` input) and kicks off the Release workflow.

Tagging by hand still works too:

```bash
git tag v0.2.0-beta.1 && git push origin v0.2.0-beta.1
```

Either way, the **Release** workflow marks it as a GitHub *pre-release* and pushes the
image as `:0.2.0-beta.1` + `:beta`. Panels on the **beta channel** pick it up;
the stable channel never sees it. CHANGELOG notes may stay under
`## [Unreleased]` until the stable release (the workflow falls back to the base
version's section, then to a generic pointer).

**Marking a bad beta:** edit the beta's GitHub release and put `[failed]` in
its title or notes (or delete the release). It then no longer counts towards
promotion, and beta-channel panels skip it.

### Stable releases

Normally **automatic**: after **4 positive betas** since the last stable
release, the **Promote beta to stable** workflow tags the latest beta's commit
as `vX.Y.Z` (the beta version without its `-beta.N` suffix) and re-runs the
Release workflow for it — full GitHub release, image tags `:x.y.z`, `:x.y`,
`:stable` and `:latest`.

To cut a stable release **earlier**, run the *Promote beta to stable* workflow
manually with **force** ticked (Actions tab) — it promotes the latest tested
beta. For a stable release straight from `main` without any beta (first
release, emergencies), use **Actions → Cut release** with channel `stable`.
Tagging by hand also still works:

```bash
git tag v0.2.0 && git push origin v0.2.0
```

Before (or right after) a stable release, move the `## [Unreleased]` notes in
[`CHANGELOG.md`](CHANGELOG.md) under a `## [x.y.z] - YYYY-MM-DD` heading and
bump `version` in `pyproject.toml` / `app/__init__.py`. (The self-updater
stamps the installed version from the release tag, so a missed bump doesn't
break updates — but keeping them in sync keeps the repo honest.)

## Reporting bugs / requesting features

Use the [issue templates](https://github.com/w0rkingchr1s/prntbtlr/issues/new/choose).
For security issues, **do not open a public issue** — see [SECURITY.md](SECURITY.md).
