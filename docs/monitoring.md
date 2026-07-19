# Monitoring PrntBtlr

PrntBtlr exposes its whole health state over a single HTTP endpoint so an
external monitoring system can watch the station without logging into the
panel. This page covers that endpoint and shows how to wire it into
[PRTG](https://www.paessler.com/prtg) — the setup the project is tuned for —
plus notes for other tools.

## The `/healthz` endpoint

`/healthz` is public (never behind the optional login) and returns JSON. It
reports two things:

* **Services** — the systemd units PrntBtlr manages (`cups`, `scanbd` /
  `prntbtlr-scan-listen`, `smbd`, `avahi-daemon`): active, enabled-on-boot, and
  a `value` of `1`/`0`.
* **Health** — the *control instances* (network, services, CUPS, printer +
  queue, scanner, AirPrint sharing, storage), each as `ok` / `warn` / `fail` /
  `skip`, plus an `overall` verdict.

```bash
curl http://prntbtlr.lan/healthz
```

```jsonc
{
  "status": "ok",
  "app": "PrntBtlr",
  "version": "…",
  "services": {
    "cups":  { "status": "active", "active": true, "enabled": true, "value": 1 },
    "smbd":  { "status": "active", "active": true, "enabled": true, "value": 1 }
    // …
  },
  "services_active": 4,
  "services_total": 5,
  "health": {
    "overall": "warn",
    "ok": 8, "warn": 1, "fail": 0,
    "repairable": 0,
    "checks": {
      "network":     { "title": "Network connection", "status": "ok",   "value": 1, "repairable": false, "detail": "…" },
      "storage":     { "title": "Scan storage",       "status": "warn", "value": 0, "repairable": false, "detail": "…" }
      // …
    }
  }
}
```

Every leaf carries a numeric `value` (`1` = healthy, `0` = needs attention) so a
sensor can alert per service and per check with a stable JSONPath key.

## PRTG

### The short version: `?format=prtg`

PRTG's **HTTP Data Advanced** sensor only accepts JSON in its own
`{"prtg": {"result": […]}}` shape. Pointing it at the plain `/healthz` payload
fails with:

> The returned JSON does not match the expected structure (Prtg is missing).
> (Code: PE231)

To avoid that, ask the endpoint for the native shape:

```
http://prntbtlr.lan/healthz?format=prtg
```

That returns exactly what the sensor expects — one channel per service and per
control instance, each with its own limits baked in, so nothing has to be
configured channel-by-channel in PRTG:

```jsonc
{
  "prtg": {
    "result": [
      { "channel": "Overall health",  "value": 1, "limitmode": 1,
        "limitminwarning": 1.5, "limitminerror": 0.5,
        "limitwarningmsg": "One or more control instances need attention",
        "limiterrormsg": "One or more control instances failed" },
      { "channel": "Services active",  "value": 4, "limitmode": 1,
        "limitminerror": 3.5, "limiterrormsg": "A required service is not running" },
      { "channel": "Checks failing",   "value": 0, "limitmode": 1, "limitmaxerror": 0.5 },
      { "channel": "Checks warning",   "value": 1, "limitmode": 1, "limitmaxwarning": 0.5 },
      { "channel": "Network connection", "value": 2, "limitmode": 1,
        "limitminwarning": 1.5, "limitminerror": 0.5 },
      { "channel": "Scan storage",     "value": 1, "limitmode": 1,
        "limitminwarning": 1.5, "limitminerror": 0.5 }
      // … one channel per control instance
    ],
    "text": "PrntBtlr: 1 warnings, 8 ok"
  }
}
```

**Values** use a 2 / 1 / 0 scale so each channel colours itself:

| Value | Meaning        | Channel state |
|:-----:|----------------|---------------|
| `2`   | `ok` / `skip`  | green         |
| `1`   | `warn`         | yellow        |
| `0`   | `fail`         | red (down)    |

The `Services active` channel counts running units and goes **red** as soon as
a required one is down. The scan-button pair (`scanbd` / `prntbtlr-scan-listen`)
shares one USB scanner, so exactly one runs and the other is idle by design —
the error limit accounts for that (one fewer than the total), so a healthy PIXMA
host showing `4` of `5` stays green. The summary `text` is shown on the sensor.

### Adding the sensor

1. In PRTG, add a device for the Pi (e.g. `prntbtlr.lan`).
2. On that device: **Add Sensor → HTTP Data Advanced**.
3. Configure:
   * **URL:** `http://prntbtlr.lan/healthz?format=prtg`
   * **Request Method:** `GET`
   * If the panel login is enabled, PRTG can't send a session cookie — keep
     `/healthz` reachable on the LAN (it is public by design), or restrict
     access at the network layer instead.
4. Save. PRTG creates one channel per entry in `result`; the limits ship inside
   the payload, so the sensor turns yellow/red on its own. Set the **scanning
   interval** to taste (e.g. 5 minutes) and add notification triggers as usual.

### Alternative: REST Custom sensor on the plain JSON

If you'd rather keep the endpoint format-agnostic, use a **REST Custom** sensor
against the default `/healthz` and map fields with JSONPath in a `*.template`
file on the PRTG probe. Useful JSONPaths:

| Channel                 | JSONPath                                          |
|-------------------------|---------------------------------------------------|
| Services active         | `$.services_active`                               |
| Health failures         | `$.health.fail`                                   |
| Health warnings         | `$.health.warn`                                   |
| CUPS running            | `$.services.cups.value`                           |
| A specific check        | `$.health.checks.storage.value`                   |

Give each numeric channel a `LimitMinError`/`LimitMaxError` in the template. The
`?format=prtg` route above is simpler because those limits come pre-set; reach
for REST Custom only if you need a bespoke channel selection.

## Other monitoring tools

`/healthz` is plain JSON, so most systems can consume it:

* **Uptime Kuma / healthchecks** — a *JSON query* HTTP monitor on
  `$.health.overall` expecting `ok` (or `$.status` = `ok` for a liveness-only
  check).
* **Prometheus** — `/healthz` is not OpenMetrics; scrape it with the Blackbox
  exporter's HTTP probe (status-code / JSON body match) or a small
  `json_exporter` config mapping the `value` fields to gauges.
* **Nagios / Icinga** — `check_http` with `--expect` on the body, or a
  `check_json`-style plugin reading `$.health.overall`.

## What each check means

The control instances and their repairs are described in the main
[README](../README.md#health-checks--self-repair). In short: `ok` = working,
`warn` = degraded but up, `fail` = broken, `skip` = not applicable on this host
(e.g. a tool or the second scan-button handler isn't installed). Skipped checks
count as healthy (`value` 2) in the PRTG output so an intentionally-idle handler
never shows as an error.
