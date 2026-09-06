#!/usr/bin/env node
/**
 * Xyron WhatsApp sidecar — programmatic open-wa client + a self-controlled
 * Express wrapper, bound to 127.0.0.1 only.
 *
 * REVISION NOTE (Stage 1 live validation): the first draft of this file
 * assumed open-wa's CLI-driven "Easy API" (booted by passing port/key into
 * create()) could be used directly. Inspecting the actually-installed
 * 4.76.0 package source (node_modules/@open-wa/wa-automate/dist/cli/*)
 * showed two things that made that wrong:
 *   1. `port`/`key`/`host` are not fields of ConfigObject at all — the Easy
 *      API is only bootable via the `wa-automate` CLI binary (dist/cli),
 *      not via create().
 *   2. The CLI's own HTTP server calls `server.listen(PORT, callback)` with
 *      NO host argument (dist/cli/index.js) — Node defaults that to
 *      0.0.0.0, i.e. all interfaces. The CLI's `--host` flag only affects
 *      the text shown in API-docs URLs, not the actual bind address. Using
 *      the CLI as originally written would have violated the
 *      localhost-only requirement below.
 *
 * Fix: use `create()` to get a Client object, mount its own documented
 * `client.middleware()` (see dist/api/Client.d.ts, `middleware` property —
 * this is the same dispatch open-wa's own Easy API uses internally) onto a
 * plain Express app THIS file controls, and call
 * `server.listen(PORT, '127.0.0.1', ...)` ourselves so the bind address is
 * explicit and verified, not delegated to a CLI default.
 *
 * client.middleware() contract (confirmed from dist/api/Client.js):
 *   POST /<methodName>  body: {"args": [positional, args]}
 *   -> {"success": true, "response": <raw client method return value>}
 *   -> {"success": false, "error": {"name","message","data"}}   (on throw)
 *   Unknown method -> HTTP 404, plain text "Cannot find method: X"
 *   There is NO built-in auth on the middleware itself — the x-api-key
 *   check below is this file's own addition, required before the
 *   middleware is reached.
 *
 * SECURITY: bound to 127.0.0.1 only. Never change the listen() host
 * argument to '0.0.0.0' or omit it — see the note above for why that
 * matters concretely, not just in principle.
 */
'use strict';

const path = require('path');
const express = require('express');
const { create } = require('@open-wa/wa-automate');

const PORT = parseInt(process.env.WA_SIDECAR_PORT || '8734', 10);
const API_KEY = process.env.WA_SIDECAR_API_KEY;
const SESSION_ID = process.env.WA_SIDECAR_SESSION_ID || 'xyron';
const SESSION_DATA_PATH =
  process.env.WA_SIDECAR_SESSION_DIR ||
  path.join(__dirname, '..', '..', '..', '..', '.secrets', 'whatsapp_openwa_session');
const HEADLESS = process.env.WA_SIDECAR_HEADLESS !== '0';

function log(event, fields) {
  // Structured, single-line JSON logs — never phone numbers, message
  // bodies, session data, or the API key itself.
  console.log(JSON.stringify({ event, ts: new Date().toISOString(), ...(fields || {}) }));
}

if (!API_KEY) {
  log('WA_SIDECAR_FAILED', { reason: 'missing_api_key' });
  console.error('WA_SIDECAR_API_KEY is not set — refusing to start an unauthenticated sidecar.');
  process.exit(1);
}

log('WA_SIDECAR_START', { sessionId: SESSION_ID, port: PORT, headless: HEADLESS });

// Live validation (Stage 2) history under WSL2 — none of these got the
// sidecar to boot cleanly there (each earned its place by being tried in
// isolation and observed to change the failure mode, not guessed
// speculatively): (1) puppeteer's own bundled Chrome via executablePath ->
// 30s waitForFunction('window.Debug...') timeout at initializer.js:208,
// page loads but WA's app never finishes initializing; (2) + useStealth /
// customUserAgent -> got further but inconsistently, sometimes the same
// timeout, sometimes a page.goto() navigation timeout; (3) mirroring the
// sibling Playwright client's WSL_ARGS via `chromiumArgs` -> open-wa's own
// startup log explicitly warned "Using custom chromium args with multi
// device will cause issues! Please remove them" (removed); headed mode via
// WSLg also failed differently (TargetCloseError right after page load).
//
// Root cause was never pinned to a single fixable flag — moved to running
// this sidecar on native Windows Node instead (per your choice), which
// sidesteps whatever WSL2/Puppeteer interaction was happening, AND lets
// Puppeteer drive your actual installed Windows Chrome rather than a Linux
// Chrome build spoofing a Windows UA. Resolution order below: your real
// Chrome install (what you asked for) > an explicit override > puppeteer's
// own downloaded Chrome as a last-resort fallback (e.g. on a machine
// without Chrome installed).
function resolveChromeExecutablePath() {
  if (process.env.WA_SIDECAR_CHROME_PATH) return process.env.WA_SIDECAR_CHROME_PATH;
  const fs = require('fs');
  const commonWindowsPaths = [
    'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
  ];
  for (const candidate of commonWindowsPaths) {
    if (fs.existsSync(candidate)) return candidate;
  }
  return require('puppeteer').executablePath(); // fallback — not what you asked for, but boots on any machine
}
const chromeExecutablePath = resolveChromeExecutablePath();

const REAL_CHROME_UA =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36';

// These ConfigObject fields were individually confirmed against
// node_modules/@open-wa/wa-automate/dist/api/model/config.d.ts for 4.76.0.
create({
  sessionId: SESSION_ID,
  headless: HEADLESS,
  executablePath: chromeExecutablePath,
  sessionDataPath: SESSION_DATA_PATH,
  multiDevice: true,
  qrTimeout: 0,          // wait indefinitely for first-time QR/link-code pairing
  authTimeout: 60,
  cacheEnabled: false,
  disableSpins: true,
  popup: false,
  useStealth: true,
  customUserAgent: REAL_CHROME_UA,
})
  .then((client) => {
    log('WA_SIDECAR_READY', { sessionId: SESSION_ID });

    const app = express();
    app.use(express.json({ limit: '25mb' })); // headroom for base64 file/image payloads

    app.get('/healthz', (_req, res) => res.json({ ok: true }));

    // Our own auth layer — client.middleware() has none of its own.
    app.use((req, res, next) => {
      if (req.path === '/healthz') return next();
      if (req.get('x-api-key') !== API_KEY) {
        return res.status(401).json({ success: false, error: { name: 'UNAUTHORIZED', message: 'bad or missing x-api-key' } });
      }
      next();
    });

    app.use(client.middleware());

    const server = app.listen(PORT, '127.0.0.1', () => {
      log('WA_SIDECAR_HTTP_READY', { port: PORT, host: '127.0.0.1' });
    });

    if (typeof client.onStateChanged === 'function') {
      client.onStateChanged((state) => {
        log('WA_SESSION_STATE', { state });
        if (['CONFLICT', 'UNLAUNCHED', 'UNPAIRED', 'UNPAIRED_IDLE'].includes(state)) {
          log('WA_SESSION_DISCONNECTED', { state });
        }
        if (state === 'CONNECTED') {
          log('WA_SESSION_READY', { sessionId: SESSION_ID });
        }
      });
    }
    if (typeof client.onLogout === 'function') {
      client.onLogout(() => log('WA_SESSION_DISCONNECTED', { reason: 'logout' }));
    }

    const shutdown = (signal) => {
      log('WA_SIDECAR_STOP', { signal });
      server.close(() => process.exit(0));
      setTimeout(() => process.exit(0), 3000).unref();
    };
    process.on('SIGTERM', () => shutdown('SIGTERM'));
    process.on('SIGINT', () => shutdown('SIGINT'));
  })
  .catch((err) => {
    log('WA_SIDECAR_FAILED', { error: String((err && err.message) || err) });
    process.exit(1);
  });

process.on('uncaughtException', (err) => {
  log('WA_SIDECAR_FAILED', { error: String((err && err.message) || err), fatal: true });
  process.exit(1);
});
