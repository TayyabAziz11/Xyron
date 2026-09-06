# WhatsApp sidecar (open-wa client.middleware())

Local Node process that runs a single open-wa session and mounts its
documented `client.middleware()` (see `Client.d.ts` in the installed
package) onto a plain Express app **this file controls**, bound to
`127.0.0.1` only. Xyron's Python transport
(`backend/api/integrations/whatsapp/openwa_transport.py`) talks to this over
HTTP — nothing else in the codebase should import `@open-wa/wa-automate`
directly.

Pinned to `4.76.0` — open-wa's own docs mark v5 as alpha ("architecture is
being reorganized into a monorepo, APIs may change between minor releases")
and name 4.76.0 as the recommended production line. Do not bump past 4.76.0
without re-verifying the contract below against the newly-installed source.

## Why not the CLI-driven "Easy API"?

The first draft of `server.js` assumed the Easy API could be booted by
passing `port`/`key` into `create()`. Inspecting the actually-installed
4.76.0 source during live validation showed that's wrong on two counts:

1. `port`/`key`/`host` aren't `ConfigObject` fields at all — the Easy API
   only boots via the `wa-automate` CLI binary (`dist/cli`).
2. The CLI's own HTTP server calls `server.listen(PORT, callback)` with
   **no host argument** (`dist/cli/index.js`) — Node defaults that to
   `0.0.0.0`, all interfaces. The `--host` CLI flag only affects the text
   shown in API-docs URLs, not the actual bind address.

Using the CLI as originally written would have listened on every interface,
directly violating the localhost-only requirement. `server.js` now uses
`create()` to get a `Client`, mounts its own `client.middleware()`
(the same dispatch the Easy API uses internally) onto an Express app we
control, and calls `server.listen(PORT, '127.0.0.1', ...)` explicitly.

## HTTP contract (verified against installed 4.76.0 source)

```
POST /<clientMethodName>   body: {"args": [positional, args, in, order]}
  -> 200 {"success": true,  "response": <raw client method return value>}
  -> 200 {"success": false, "error": {"name","message","data"}}   (thrown)
  -> 404 "Cannot find method: X"   (plain text)
```

`client.middleware()` has no auth of its own — `server.js` adds an
`x-api-key` check in front of it. Methods used by the Python transport
(`sendText`, `sendFile`, `sendImage`, `reply`, `getAllUnreadMessages`,
`getAllChats`, `getAllContacts`, `sendSeen`, `isConnected`,
`getConnectionState`) were each confirmed to exist with this exact
name/argument order in `dist/api/Client.d.ts`.

A `success: true` envelope does not always mean the WhatsApp action
succeeded — several methods are `Promise<boolean>` and can resolve `false`
without throwing. `openwa_transport.py`'s `_send_via_rpc` checks for this
explicitly for send-type actions.

## Setup

```bash
cd backend/integrations/whatsapp/sidecar
npm install
cp .env.example .env
# edit .env — set a real WA_SIDECAR_API_KEY, then keep it in sync with
# WA_SIDECAR_API_KEY in backend/.env (same value, both sides)
```

## Run

```bash
node server.js
```

First run prints/opens a QR code (or use open-wa's phone-pairing flow if
running headless with no display — see open-wa's own docs for the flag).
Session persists under `.secrets/whatsapp_openwa_session/` (gitignored) —
no re-pairing needed on restart.

## Security

- `server.js` binds `server.listen(PORT, '127.0.0.1', ...)` explicitly —
  never remove the host argument or change it to `'0.0.0.0'`. See "Why not
  the CLI-driven Easy API?" above for what goes wrong if you do.
- `WA_SIDECAR_API_KEY` is a shared secret between this process and the
  Python transport, checked by `server.js`'s own middleware (not something
  open-wa provides). Do not commit `.env`.
- `npm audit` currently reports vulnerabilities in transitive dependencies
  (deprecated `uuid`/`rimraf`/`glob`, older `puppeteer`) — not fixed here
  since `npm audit fix --force` could silently move past the pinned 4.76.0.
  Review before running this in any context beyond local dev.

## Known gaps (Step 1)

- `dist/api/Client.js`'s exact success-response envelope for every method
  was spot-checked, not exhaustively tested against a live session — first
  real send is expected to be the final confirmation.
