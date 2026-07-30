# Webhooks

PrntBtlr can POST a small JSON payload to any URL when something happens — a
scan finishes, a print job starts or completes, health changes, an update
appears. It's the glue for wiring the panel into [n8n](https://n8n.io),
[Home Assistant](https://www.home-assistant.io), a Discord/Slack relay, or your
own script.

This page documents the payload of every event and walks through ready-to-import
**n8n** and **Home Assistant** recipes.

- [Setting one up](#setting-one-up)
- [The request](#the-request) — envelope, headers, signature
- [Events & payloads](#events--payloads)
- [Verifying the signature](#verifying-the-signature)
- [Recipe: n8n → Telegram](#recipe-n8n--telegram)
- [Recipe: Home Assistant](#recipe-home-assistant)
- [Configuration](#configuration)

---

## Setting one up

**System → Webhooks** on the panel:

1. Paste the **Payload URL** (e.g. your n8n webhook URL).
2. Tick the **events** it should receive.
3. Optionally set a **secret** — the body is then signed so the receiver can
   verify it really came from your panel.
4. **Add**, then hit **Test** to send a sample `webhook.test` event and confirm
   the wiring before relying on it.

Deliveries run in the background off a small thread pool, so a slow or offline
endpoint never holds up the panel or a scan. Endpoints are stored in
`/etc/prntbtlr/webhooks.json`.

---

## The request

Every delivery is an HTTP `POST` with a JSON body in this envelope:

```json
{
  "event": "scan.completed",
  "timestamp": "2026-07-30T09:15:04Z",
  "app": "PrntBtlr",
  "version": "0.1.0",
  "host": "prntbtlr",
  "data": { }
}
```

| Field | Meaning |
| --- | --- |
| `event` | The event key (see below). |
| `timestamp` | UTC, ISO-8601 (`…Z`). |
| `app` | Always `PrntBtlr` (your `PRNTBTLR_APP_NAME`). |
| `version` | Panel version that sent it. |
| `host` | The station's hostname — handy when several report to one endpoint. |
| `data` | Event-specific fields, documented per event below. |

### Headers

| Header | Value |
| --- | --- |
| `Content-Type` | `application/json` |
| `User-Agent` | `prntbtlr/<version>` |
| `X-Prntbtlr-Event` | the event key (same as `event`) |
| `X-Prntbtlr-Delivery` | a random id, unique per delivery attempt |
| `X-Prntbtlr-Signature` | `sha256=<hmac>` — **only when a secret is set** |

> HTTP header names are case-insensitive; some clients/proxies show them
> lower-cased (`x-prntbtlr-signature`). Match them case-insensitively.

---

## Events & payloads

The `data` object per event. Fields are shown with example values.

### Scanning

**`scan.completed`** — a scan finished, from the browser **or** the hardware scan
button.

```json
// browser scan
{ "file": "scan_20260730_091504.pdf", "mode": "Color", "resolution": 300, "source": "Flatbed" }

// button scan (values arrive as strings; adds source=button, pages, ocr)
{ "file": "scan_20260730_091504.pdf", "mode": "Color", "source": "button", "pages": "2", "ocr": "0" }
```

### Printers

**`printer.added`**

```json
{ "name": "MX870", "uri": "usb://Canon/MX870%20series?serial=1A2B3C", "shared": true }
```

**`printer.deleted`**

```json
{ "name": "MX870" }
```

**`print.submitted`** — any real job was queued (AirPrint, `lp`, or the panel's
test page).
**`print.completed`** — that job finished printing.

```json
{ "job": "MX870-7", "printer": "MX870", "user": "pi", "size": 40960, "when": "Thu 30 Jul 2026 09:15:00" }
```

> `job` is the CUPS job id. `size` is in bytes. Both events carry the same shape;
> a fast job may deliver `print.submitted` and `print.completed` in quick
> succession.

### Health

**`health.degraded`** — overall health crossed into warning/failure.
**`health.recovered`** — it went back to ok.

```json
{ "overall": "warn", "previous": "ok" }
```

`overall`/`previous` are one of `ok` / `warn` / `fail`.

**`repair.performed`** — self-repair took action.

```json
{
  "actions": [
    { "title": "Restart cups", "ok": true, "message": "CUPS restarted." }
  ],
  "overall": "ok"
}
```

### Updates

**`update.available`** — a newer release was found on the configured channel.

```json
{ "tag": "v0.2.0", "version": "0.2.0", "channel": "stable" }
```

**`update.applied`** — an update was started from the panel.

```json
{ "tag": "v0.2.0", "from": "0.1.0" }
```

### Test

**`webhook.test`** — sent by the **Test** button.

```json
{ "message": "This is a PrntBtlr test event." }
```

---

## Verifying the signature

When an endpoint has a secret, PrntBtlr signs the **raw request body** with
HMAC-SHA256 and sends it as `X-Prntbtlr-Signature: sha256=<hex>`.

> **Verify over the raw bytes you received, not a re-serialized copy.** Like
> GitHub's webhooks, the HMAC is computed over the exact body on the wire. If you
> parse the JSON and re-encode it, whitespace and key order change and the digest
> won't match.

Python:

```python
import hmac, hashlib


def valid(raw_body: bytes, header: str, secret: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header or "")
```

Node.js:

```js
const crypto = require('crypto');
function valid(rawBody, header, secret) {
  const expected = 'sha256=' + crypto.createHmac('sha256', secret).update(rawBody).digest('hex');
  const a = Buffer.from(expected), b = Buffer.from(header || '');
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}
```

---

## Recipe: n8n → Telegram

Turn any PrntBtlr event into a Telegram message. On a trusted LAN the random n8n
webhook URL is already a shared secret, so start without signing; the
[hardening step](#hardening-verify-the-signature-in-n8n) below adds HMAC checking.

### 1. Build the workflow

Three nodes: **Webhook → Code (format message) → Telegram**.

- **Webhook** (`POST`, path `prntbtlr`): its Production URL —
  `https://<your-n8n>/webhook/prntbtlr` — is what you paste into PrntBtlr.
- **Code**: turns the event + `data` into a German one-liner.
- **Telegram**: sends it (needs a Bot credential and your chat id).

The Code node (parsed body is at `$json.body`):

```js
const p = $json.body;
const d = p.data || {};
let msg;
switch (p.event) {
  case 'scan.completed':  msg = `📄 Scan fertig: ${d.file}`; break;
  case 'print.submitted': msg = `🖨️ Druck gestartet: Job ${d.job} auf ${d.printer}`; break;
  case 'print.completed': msg = `✅ Druck fertig: Job ${d.job} auf ${d.printer}`; break;
  case 'printer.added':   msg = `➕ Drucker angelegt: ${d.name}`; break;
  case 'health.degraded': msg = `⚠️ Health: ${d.previous} → ${d.overall}`; break;
  case 'health.recovered':msg = `✅ Health wieder ok (${d.previous} → ${d.overall})`; break;
  case 'update.available': msg = `🔔 Update verfügbar: ${d.tag}`; break;
  case 'webhook.test':    msg = `🧪 Test: ${d.message}`; break;
  default:                msg = `PrntBtlr: ${p.event}`;
}
return [{ json: { text: `${msg}\n(${p.host} · ${p.timestamp})` } }];
```

The Telegram node's **Text** is `={{ $json.text }}`.

### 2. Import it

Paste this into **n8n → Workflows → Import from Clipboard**, then open the
Telegram node and pick your Bot credential + chat id. Copy the Webhook node's
Production URL into **PrntBtlr → System → Webhooks** and tick the events you want.

```json
{
  "name": "PrntBtlr → Telegram",
  "nodes": [
    {
      "parameters": {
        "httpMethod": "POST",
        "path": "prntbtlr",
        "options": {}
      },
      "id": "webhook-in",
      "name": "PrntBtlr Webhook",
      "type": "n8n-nodes-base.webhook",
      "typeVersion": 2,
      "position": [420, 300]
    },
    {
      "parameters": {
        "jsCode": "const p = $json.body;\nconst d = p.data || {};\nlet msg;\nswitch (p.event) {\n  case 'scan.completed':  msg = `📄 Scan fertig: ${d.file}`; break;\n  case 'print.submitted': msg = `🖨️ Druck gestartet: Job ${d.job} auf ${d.printer}`; break;\n  case 'print.completed': msg = `✅ Druck fertig: Job ${d.job} auf ${d.printer}`; break;\n  case 'printer.added':   msg = `➕ Drucker angelegt: ${d.name}`; break;\n  case 'health.degraded': msg = `⚠️ Health: ${d.previous} → ${d.overall}`; break;\n  case 'health.recovered':msg = `✅ Health wieder ok (${d.previous} → ${d.overall})`; break;\n  case 'update.available': msg = `🔔 Update verfügbar: ${d.tag}`; break;\n  case 'webhook.test':    msg = `🧪 Test: ${d.message}`; break;\n  default:                msg = `PrntBtlr: ${p.event}`;\n}\nreturn [{ json: { text: `${msg}\\n(${p.host} · ${p.timestamp})` } }];"
      },
      "id": "format-msg",
      "name": "Format message",
      "type": "n8n-nodes-base.code",
      "typeVersion": 2,
      "position": [660, 300]
    },
    {
      "parameters": {
        "chatId": "YOUR_CHAT_ID",
        "text": "={{ $json.text }}",
        "additionalFields": {}
      },
      "id": "telegram-out",
      "name": "Telegram",
      "type": "n8n-nodes-base.telegram",
      "typeVersion": 1.2,
      "position": [900, 300]
    }
  ],
  "connections": {
    "PrntBtlr Webhook": { "main": [[{ "node": "Format message", "type": "main", "index": 0 }]] },
    "Format message": { "main": [[{ "node": "Telegram", "type": "main", "index": 0 }]] }
  },
  "settings": {}
}
```

### Hardening: verify the signature in n8n

For an endpoint reachable beyond your LAN, set a **secret** on the PrntBtlr
webhook and check it in n8n before acting:

1. On the **Webhook** node, open **Options → Raw Body** and enable it — the Code
   node then sees the exact bytes that were signed (parsing + re-encoding would
   break the HMAC, see [above](#verifying-the-signature)).
2. Insert a **Code** node right after the webhook:

```js
const crypto = require('crypto');
const secret = 'YOUR_WEBHOOK_SECRET';            // same value as in PrntBtlr

const item  = $input.first();
const raw    = Buffer.from(item.binary.data.data, 'base64');   // Raw Body (base64)
const header = item.json.headers['x-prntbtlr-signature'] || '';
const expect = 'sha256=' + crypto.createHmac('sha256', secret).update(raw).digest('hex');

const a = Buffer.from(expect), b = Buffer.from(header);
if (a.length !== b.length || !crypto.timingSafeEqual(a, b)) {
  throw new Error('Invalid PrntBtlr signature');
}
// hand the parsed payload downstream under `body`, like the default webhook
return [{ json: { body: JSON.parse(raw.toString('utf8')) } }];
```

---

## Recipe: Home Assistant

Home Assistant receives webhooks through an **automation with a webhook
trigger** — no add-on required. The `webhook_id` *is* the secret: keep it long
and unguessable, and leave `local_only` on when the panel and HA share a LAN.

Add this to `automations.yaml` (or **Settings → Automations → ⋮ → Edit in YAML**),
then reload automations:

```yaml
alias: PrntBtlr notifications
trigger:
  - platform: webhook
    webhook_id: prntbtlr-CHANGE-ME-to-something-random
    allowed_methods: [POST]
    local_only: true # PrntBtlr is on the LAN; set false if HA is reached remotely
action:
  - service: notify.notify # or notify.mobile_app_<your-device>
    data:
      title: PrntBtlr
      message: >-
        {% set e = trigger.json.event %}
        {% set d = trigger.json.data %}
        {% if e == 'scan.completed' %}📄 Scan fertig: {{ d.file }}
        {% elif e == 'print.submitted' %}🖨️ Druck gestartet: Job {{ d.job }} auf {{ d.printer }}
        {% elif e == 'print.completed' %}✅ Druck fertig: Job {{ d.job }} auf {{ d.printer }}
        {% elif e == 'printer.added' %}➕ Drucker angelegt: {{ d.name }}
        {% elif e == 'health.degraded' %}⚠️ Health: {{ d.previous }} → {{ d.overall }}
        {% elif e == 'health.recovered' %}✅ Health wieder ok ({{ d.previous }} → {{ d.overall }})
        {% elif e == 'update.available' %}🔔 Update verfügbar: {{ d.tag }}
        {% elif e == 'webhook.test' %}🧪 Test: {{ d.message }}
        {% else %}PrntBtlr: {{ e }}{% endif %}
```

The URL to paste into **PrntBtlr → System → Webhooks** is:

```
http://<your-ha>:8123/api/webhook/prntbtlr-CHANGE-ME-to-something-random
```

PrntBtlr sends `Content-Type: application/json`, so the parsed body is available
to templates as `trigger.json` (and the headers as `trigger.headers`).

### Only notify on some events

Filter in the trigger so the automation fires only for the events you want — say
finished prints and health problems:

```yaml
condition:
  - "{{ trigger.json.event in ['print.completed', 'health.degraded'] }}"
```

> **Signing.** Home Assistant's webhook trigger can't verify an HMAC signature in
> a template (Jinja has no HMAC function), so leave the PrntBtlr **secret** empty
> for this recipe and rely on the unguessable `webhook_id` plus `local_only`. If
> you need signature verification, terminate the webhook at a reverse proxy (or
> AppDaemon / pyscript) that checks `X-Prntbtlr-Signature` — see
> [Verifying the signature](#verifying-the-signature) — then forwards to HA.

---

## Configuration

All optional; defaults suit the installer's layout.

| Env var | Default | What it does |
| --- | --- | --- |
| `PRNTBTLR_WEBHOOK_STATE_FILE` | `/etc/prntbtlr/webhooks.json` | Where endpoints are stored. |
| `PRNTBTLR_WEBHOOK_TIMEOUT` | `10` | Per-delivery HTTP timeout (seconds). |
| `PRNTBTLR_WEBHOOK_HEALTH_INTERVAL` | `60` | How often health is polled for the `health.*` events (only while subscribed). |
| `PRNTBTLR_WEBHOOK_JOBS_INTERVAL` | `15` | How often CUPS is polled for the `print.*` events (only while subscribed). |

The `health.*` and `print.*` monitors do nothing until an enabled endpoint
subscribes to one of their events, and each records a baseline on start so a
restart never replays existing state.
