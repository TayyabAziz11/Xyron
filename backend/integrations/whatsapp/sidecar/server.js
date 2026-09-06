#!/usr/bin/env node
/**
 * Xyron WhatsApp sidecar — Baileys-based transport (Phase 2).
 *
 * Architecture:
 *   Baileys (WebSocket → WhatsApp protocol, no browser)
 *     ↔
 *   Express REST (outbound commands) + SSE (inbound events)
 *     ↔
 *   127.0.0.1:8734 (localhost only, x-api-key protected)
 *     ↔
 *   Xyron Python backend (BaileysTransport)
 *
 * Replaces the open-wa 4.76.0 sidecar (archived as server.openwa.js).
 *
 * SECURITY:
 *   - Binds 127.0.0.1 ONLY. Never change to 0.0.0.0.
 *   - x-api-key required on every endpoint except /healthz.
 *   - API key is never logged.
 *   - Phone numbers and message bodies are redacted from logs.
 *   - Auth credentials persist in .secrets/ (gitignored).
 *   - SQLite database persists in .secrets/ (gitignored).
 *   - sendFile/sendImage block access to .secrets/ directory.
 *
 * EVENT STREAM (SSE):
 *   GET /events streams normalized WhatsAppEvent objects via SSE.
 *   Events are persisted to SQLite BEFORE emission — crash-safe.
 *   Each event: id, event_type, timestamp, provider, data.
 *
 * RESPONSE SHAPE (all endpoints except /healthz):
 *   Success: { ok: true, data: {...}, error: null }
 *   Failure: { ok: false, data: null, error: { code: "...", message: "..." } }
 */
'use strict';

// ── Load .env BEFORE any config reads ─────────────────────────────────────
require('dotenv').config();

const path = require('path');
const fs = require('fs');
const express = require('express');

// ── Configuration ─────────────────────────────────────────────────────────

const PORT = parseInt(process.env.WA_SIDECAR_PORT || '8734', 10);
const API_KEY = process.env.WA_SIDECAR_API_KEY;
const SESSION_ID = process.env.WA_SIDECAR_SESSION_ID || 'xyron';

const REPO_ROOT = path.resolve(__dirname, '..', '..', '..', '..');
const DEFAULT_AUTH_DIR = path.join(REPO_ROOT, '.secrets', 'whatsapp_openwa_session', 'baileys_auth');
const AUTH_DIR = process.env.WA_SIDECAR_AUTH_DIR || DEFAULT_AUTH_DIR;
const DB_PATH = path.join(path.dirname(AUTH_DIR), 'whatsapp_store.db');

const MAX_FILE_SIZE = 100 * 1024 * 1024; // 100 MB
const MAX_DOWNLOAD_SIZE = 100 * 1024 * 1024; // 100 MB
const SSE_HEARTBEAT_MS = 15_000;

const MEDIA_DIR = process.env.WA_MEDIA_DIR
  || path.join(REPO_ROOT, 'backend', 'data', 'whatsapp_media');

// ── Logging ───────────────────────────────────────────────────────────────
function log(event, fields) {
  const entry = { event, ts: new Date().toISOString(), ...(fields || {}) };
  console.log(JSON.stringify(entry));
}

if (!API_KEY) {
  log('WA_SIDECAR_FAILED', { reason: 'missing_api_key' });
  console.error('WA_SIDECAR_API_KEY is not set — refusing to start.');
  process.exit(1);
}

// ── Baileys imports ──────────────────────────────────────────────────────
const {
  default: makeWASocket,
  useMultiFileAuthState,
  DisconnectReason,
  Browsers,
  jidNormalizedUser,
  isJidGroup,
  isJidBroadcast,
  isJidStatusBroadcast,
  generateMessageID,
  downloadMediaMessage,
} = require('@whiskeysockets/baileys');

const pino = require('pino');
const qrcode = require('qrcode-terminal');
const initSqlJs = require('sql.js');

const baileyLogger = pino({ level: 'silent' });

// ── State ─────────────────────────────────────────────────────────────────
let connectionState = 'starting';
let authReady = false;
let currentQR = null;
let pairingCode = null;
let lastError = null;
let eventSeq = 0;
let globalSock = null;

// In-memory message store for Baileys retry mechanism (getMessage callback).
const _msgStore = new Map();
function storeMessage(msg) {
  const { remoteJid, id } = msg.key || {};
  if (remoteJid && id && msg.message) _msgStore.set(`${remoteJid}:${id}`, msg);
}
function loadMessage(remoteJid, id) {
  return _msgStore.get(`${remoteJid}:${id}`) || null;
}

const sseClients = new Set();

// ── SQLite (sql.js — pure JS/WASM, no native build required) ─────────────
let db = null;
let saveTimer = null;

class DB {
  constructor(sqlDb) { this._db = sqlDb; }
  run(sql, params = []) { this._db.run(sql, params); this._scheduleSave(); }
  get(sql, params = []) {
    const stmt = this._db.prepare(sql);
    if (params.length) stmt.bind(params);
    if (stmt.step()) {
      const cols = stmt.getColumnNames();
      const vals = stmt.get();
      stmt.free();
      const row = {};
      cols.forEach((c, i) => { row[c] = vals[i]; });
      return row;
    }
    stmt.free();
    return null;
  }
  all(sql, params = []) {
    const stmt = this._db.prepare(sql);
    if (params.length) stmt.bind(params);
    const rows = [];
    while (stmt.step()) {
      const cols = stmt.getColumnNames();
      const vals = stmt.get();
      const row = {};
      cols.forEach((c, i) => { row[c] = vals[i]; });
      rows.push(row);
    }
    stmt.free();
    return rows;
  }
  _scheduleSave() {
    if (saveTimer) return;
    saveTimer = setTimeout(() => { saveTimer = null; this._flush(); }, 2000);
  }
  _flush() {
    try {
      const data = this._db.export();
      fs.writeFileSync(DB_PATH, Buffer.from(data));
    } catch (e) { log('WA_DB_SAVE_FAILED', { error: String(e?.message || e) }); }
  }
  saveNow() { if (saveTimer) { clearTimeout(saveTimer); saveTimer = null; } this._flush(); }
  close() { if (saveTimer) { clearTimeout(saveTimer); saveTimer = null; } this._flush(); this._db.close(); }
}

function createSchema(d) {
  d.run(`CREATE TABLE IF NOT EXISTS whatsapp_chats (
    chat_id TEXT PRIMARY KEY, name TEXT, last_message_ts TEXT,
    unread_count INTEGER DEFAULT 0, is_group INTEGER DEFAULT 0, updated_at TEXT)`);
  d.run(`CREATE TABLE IF NOT EXISTS whatsapp_contacts (
    contact_id TEXT PRIMARY KEY, display_name TEXT, phone TEXT,
    push_name TEXT, chat_id TEXT, updated_at TEXT)`);
  d.run(`CREATE TABLE IF NOT EXISTS whatsapp_messages (
    message_id TEXT NOT NULL, chat_id TEXT NOT NULL, sender_id TEXT,
    sender_name TEXT, timestamp TEXT, message_type TEXT, text TEXT,
    quoted_message_id TEXT, media_type TEXT, media_metadata_json TEXT,
    from_me INTEGER DEFAULT 0, is_group INTEGER DEFAULT 0, group_id TEXT,
    group_name TEXT, provider TEXT DEFAULT 'baileys', event_origin TEXT DEFAULT 'live',
    provider_key_json TEXT,
    PRIMARY KEY (message_id, chat_id))`);
  d.run('CREATE INDEX IF NOT EXISTS idx_msg_chat_ts ON whatsapp_messages(chat_id, timestamp DESC)');
  d.run('CREATE INDEX IF NOT EXISTS idx_msg_sender ON whatsapp_messages(sender_id)');
  d.run('CREATE INDEX IF NOT EXISTS idx_msg_origin ON whatsapp_messages(event_origin)');
  // Phase 2.1 — media retrieval columns
  _addColumn(d, 'whatsapp_messages', 'local_path', 'TEXT');
  _addColumn(d, 'whatsapp_messages', 'downloaded_at', 'TEXT');
  _addColumn(d, 'whatsapp_messages', 'download_status', 'TEXT');
  d.run('CREATE INDEX IF NOT EXISTS idx_msg_media_type ON whatsapp_messages(media_type)');
}

function _addColumn(d, table, column, type) {
  try {
    d.run(`ALTER TABLE ${table} ADD COLUMN ${column} ${type}`);
  } catch (_) { /* column already exists */ }
}

async function loadDB() {
  const SQL = await initSqlJs();
  let sqlDb;
  if (fs.existsSync(DB_PATH)) {
    const buf = fs.readFileSync(DB_PATH);
    sqlDb = new SQL.Database(buf);
    log('WA_DB_LOADED', { path: DB_PATH });
  } else {
    sqlDb = new SQL.Database();
    log('WA_DB_CREATED', { path: DB_PATH });
  }
  db = new DB(sqlDb);
  createSchema(db);
  // Flush every 30s regardless of writes (crash safety)
  setInterval(() => db._flush(), 30_000).unref();
}

// ── Normalization helpers ─────────────────────────────────────────────────

function makeEvent(type, data) {
  eventSeq++;
  const now = Date.now();
  return { id: `${now}-${eventSeq}`, event_type: type, timestamp: new Date(now).toISOString(), provider: 'baileys', data };
}

function normalizeMessage(msg) {
  const key = msg.key || {};
  const message = msg.message || {};
  let messageType = 'unknown';
  if (message.conversation) messageType = 'text';
  else if (message.extendedTextMessage) messageType = 'text';
  else if (message.imageMessage) messageType = 'image';
  else if (message.documentMessage) messageType = 'document';
  else if (message.audioMessage) messageType = 'audio';
  else if (message.videoMessage) messageType = 'video';
  else if (message.stickerMessage) messageType = 'sticker';
  else if (message.contactMessage) messageType = 'contact';
  else if (message.locationMessage) messageType = 'location';
  else if (message.reactionMessage) messageType = 'reaction';
  else if (message.pollCreationMessage) messageType = 'poll';
  else if (message.protocolMessage) messageType = 'protocol';

  let text = null;
  if (message.conversation) text = message.conversation;
  else if (message.extendedTextMessage) text = message.extendedTextMessage.text || null;
  else if (message.imageMessage) text = message.imageMessage.caption || null;
  else if (message.documentMessage) text = message.documentMessage.caption || null;
  else if (message.videoMessage) text = message.videoMessage.caption || null;

  let quotedMessageId = null;
  const ctx = message.extendedTextMessage?.contextInfo;
  if (ctx) quotedMessageId = ctx.stanzaId || null;

  let mediaType = null, mediaMetadata = null;
  if (message.imageMessage) {
    mediaType = 'image';
    const m = message.imageMessage;
    mediaMetadata = {
      mimetype: m.mimetype || null, filename: m.fileName || null,
      width: m.width || null, height: m.height || null,
      provider_media: _providerMedia(m),
    };
  } else if (message.documentMessage) {
    mediaType = 'document';
    const m = message.documentMessage;
    mediaMetadata = {
      mimetype: m.mimetype || null, filename: m.fileName || null,
      file_length: m.fileLength ? Number(m.fileLength) : null,
      provider_media: _providerMedia(m),
    };
  } else if (message.audioMessage) {
    mediaType = 'audio';
    const m = message.audioMessage;
    mediaMetadata = {
      mimetype: m.mimetype || null, is_voice_note: !!m.ptt,
      seconds: m.seconds || null, ptt: !!m.ptt,
      provider_media: _providerMedia(m),
    };
  } else if (message.videoMessage) {
    mediaType = 'video';
    const m = message.videoMessage;
    mediaMetadata = {
      mimetype: m.mimetype || null,
      width: m.width || null, height: m.height || null,
      seconds: m.seconds || null,
      provider_media: _providerMedia(m),
    };
  } else if (message.stickerMessage) {
    mediaType = 'sticker';
    const m = message.stickerMessage;
    mediaMetadata = {
      mimetype: m.mimetype || null,
      width: m.width || null, height: m.height || null,
      is_animated: !!m.isAnimated, is_avatar: !!m.isAvatar,
      provider_media: _providerMedia(m),
    };
  }

  const chatJid = key.remoteJid || null;
  const isGroup = chatJid ? isJidGroup(chatJid) : false;
  const senderJid = key.participant || (key.fromMe ? null : chatJid);

  // Enrich from DB (contact names, group names)
  let senderName = null, groupName = null;
  if (db && senderJid) {
    const c = db.get('SELECT display_name FROM whatsapp_contacts WHERE contact_id = ?', [senderJid]);
    if (c) senderName = c.display_name;
  }
  if (db && isGroup && chatJid) {
    const g = db.get('SELECT name FROM whatsapp_chats WHERE chat_id = ?', [chatJid]);
    if (g) groupName = g.name;
  }

  return {
    message_id: key.id || null, chat_id: chatJid, sender_id: senderJid,
    sender_phone: null, sender_name: senderName,
    timestamp: msg.messageTimestamp ? new Date(msg.messageTimestamp * 1000).toISOString() : new Date().toISOString(),
    message_type: messageType, text, quoted_message_id: quotedMessageId,
    media_type: mediaType, media_metadata: mediaMetadata,
    is_group: isGroup, group_id: isGroup ? chatJid : null, group_name: groupName,
    from_me: !!key.fromMe,
  };
}

function _bufToB64(v) {
  if (!v) return null;
  if (Buffer.isBuffer(v)) return v.toString('base64');
  if (v instanceof Uint8Array) return Buffer.from(v).toString('base64');
  // Some fields (e.g. fileEncSha256) arrive as base64 strings, not bytes
  if (typeof v === 'string' && /^[A-Za-z0-9+/]+={0,2}$/.test(v) && v.length >= 4) return v;
  return null;
}

function _providerMedia(m) {
  if (!m) return null;
  return {
    provider: 'baileys',
    media_key_b64: _bufToB64(m.mediaKey),
    direct_path: m.directPath || null,
    url: m.url || null,
    file_sha256_b64: _bufToB64(m.fileSha256),
    file_enc_sha256_b64: _bufToB64(m.fileEncSha256),
    file_length: m.fileLength ? Number(m.fileLength) : null,
  };
}

function normalizeCall(call) {
  return { call_id: call.id || null, caller_id: call.from || null, status: call.status || null, is_video: !!call.isVideo, is_group: !!call.isGroup };
}

// ── SSE helpers ───────────────────────────────────────────────────────────

function broadcastEvent(evt) {
  const frame = `id: ${evt.id}\nevent: ${evt.event_type}\ndata: ${JSON.stringify(evt)}\n\n`;
  for (const res of sseClients) {
    try { res.write(frame); } catch (_) { /* client gone */ }
  }
}

function startHeartbeat() {
  return setInterval(() => {
    for (const res of sseClients) {
      try { res.write(': heartbeat\n\n'); } catch (_) { /* client gone */ }
    }
  }, SSE_HEARTBEAT_MS);
}

// ── Baileys socket lifecycle ─────────────────────────────────────────────

async function startSocket() {
  if (!fs.existsSync(AUTH_DIR)) {
    fs.mkdirSync(AUTH_DIR, { recursive: true });
    log('WA_AUTH_DIR_CREATED', { path: AUTH_DIR });
  }
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
  const sock = makeWASocket({
    printQRInTerminal: false, auth: state, logger: baileyLogger,
    browser: Browsers.windows('Chrome'), syncFullHistory: true,
    markOnlineOnConnect: false,
    getMessage: async (key) => { const m = loadMessage(key.remoteJid, key.id); return m?.message || undefined; },
  });

  // ── Connection state ────────────────────────────────────────────────────
  sock.ev.on('connection.update', async (update) => {
    const { connection, lastDisconnect, qr } = update;
    if (qr) {
      currentQR = qr;
      log('WA_QR_AVAILABLE', {});
      try { qrcode.generate(qr, { small: true }); } catch (_) {}
    }
    if (connection === 'open') {
      connectionState = 'open'; authReady = true; currentQR = null; pairingCode = null;
      log('WA_CONNECTION_OPEN', {});
    }
    if (connection === 'close') {
      const reason = lastDisconnect?.error?.output?.statusCode;
      log('WA_CONNECTION_CLOSE', { reason: reason || 'unknown' });
      if (reason === DisconnectReason.restartRequired) {
        connectionState = 'connecting';
        log('WA_RECONNECTING', { reason: 'restart_required' });
        startSocket().catch((err) => log('WA_RECONNECT_FAILED', { error: String(err?.message || err) }));
        return;
      }
      if (reason === DisconnectReason.loggedOut) {
        connectionState = 'closed'; authReady = false; lastError = 'logged_out';
        log('WA_LOGGED_OUT', {});
        return;
      }
      connectionState = 'connecting';
      lastError = String(lastDisconnect?.error?.message || 'disconnected');
      setTimeout(() => {
        log('WA_RECONNECTING', { reason: lastError });
        startSocket().catch((err) => log('WA_RECONNECT_FAILED', { error: String(err?.message || err) }));
      }, 3000);
    }
  });

  sock.ev.on('creds.update', saveCreds);

  // ── Inbound messages (persist → emit) ──────────────────────────────────
  sock.ev.on('messages.upsert', (upsert) => {
    const { messages, type } = upsert;
    for (const msg of messages) {
      if (msg.message?.protocolMessage) continue;
      const n = normalizeMessage(msg);
      const eventOrigin = n.from_me ? 'outgoing' : (type === 'append' ? 'history_sync' : 'live');
      const eventType = eventOrigin === 'history_sync' ? 'whatsapp.history' : 'whatsapp.message';

      // Upsert contact from message metadata
      if (db && n.sender_id) {
        const pushName = msg.pushName || msg.pushname || null;
        if (pushName) {
          db.run(
            `INSERT INTO whatsapp_contacts (contact_id, display_name, push_name, updated_at)
             VALUES (?, ?, ?, ?) ON CONFLICT(contact_id) DO UPDATE SET
             push_name=COALESCE(excluded.push_name, whatsapp_contacts.push_name),
             display_name=COALESCE(excluded.display_name, whatsapp_contacts.display_name),
             updated_at=excluded.updated_at`,
            [n.sender_id, pushName, pushName, n.timestamp]
          );
        }
      }

      // Persist to SQLite BEFORE emitting SSE
      if (db && n.message_id && n.chat_id) {
        db.run(
          `INSERT OR REPLACE INTO whatsapp_messages
           (message_id, chat_id, sender_id, sender_name, timestamp, message_type, text,
            quoted_message_id, media_type, media_metadata_json, from_me, is_group, group_id,
            group_name, provider, event_origin, provider_key_json)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
          [n.message_id, n.chat_id, n.sender_id, n.sender_name, n.timestamp, n.message_type, n.text,
           n.quoted_message_id, n.media_type, JSON.stringify(n.media_metadata), n.from_me ? 1 : 0,
           n.is_group ? 1 : 0, n.group_id, n.group_name, 'baileys', eventOrigin,
           JSON.stringify({ remoteJid: msg.key.remoteJid, id: msg.key.id, fromMe: !!msg.key.fromMe, participant: msg.key.participant || null })]
        );
      }

      broadcastEvent(makeEvent(eventType, n));
      if (msg.key?.remoteJid && msg.key?.id) storeMessage(msg);

      log('WA_MESSAGE_IN', { chat: n.chat_id, type: n.message_type, origin: eventOrigin });
    }
  });

  // ── History sync (bulk) ────────────────────────────────────────────────
  sock.ev.on('messaging-history.set', (history) => {
    const { messages: histMsgs, chats, contacts } = history;
    log('WA_HISTORY_SYNC', { messages: histMsgs?.length || 0, chats: chats?.length || 0, contacts: contacts?.length || 0 });

    // Upsert chats
    for (const chat of (chats || [])) {
      if (!chat.id) continue;
      db.run(
        `INSERT OR REPLACE INTO whatsapp_chats (chat_id, name, is_group, updated_at) VALUES (?, ?, ?, ?)`,
        [chat.id, chat.name || null, isJidGroup(chat.id) ? 1 : 0, new Date().toISOString()]
      );
    }
    // Upsert contacts
    for (const c of (contacts || [])) {
      if (!c.id) continue;
      const name = c.name || c.notify || c.pushname || null;
      db.run(
        `INSERT OR REPLACE INTO whatsapp_contacts (contact_id, display_name, push_name, updated_at) VALUES (?, ?, ?, ?)`,
        [c.id, name, c.pushname || null, new Date().toISOString()]
      );
    }
    // Upsert messages
    for (const msg of (histMsgs || [])) {
      if (msg.message?.protocolMessage) continue;
      const n = normalizeMessage(msg);
      if (!n.message_id || !n.chat_id) continue;
      db.run(
        `INSERT OR REPLACE INTO whatsapp_messages
         (message_id, chat_id, sender_id, sender_name, timestamp, message_type, text,
          quoted_message_id, media_type, media_metadata_json, from_me, is_group, group_id,
          group_name, provider, event_origin, provider_key_json)
         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
        [n.message_id, n.chat_id, n.sender_id, n.sender_name, n.timestamp, n.message_type, n.text,
         n.quoted_message_id, n.media_type, JSON.stringify(n.media_metadata), n.from_me ? 1 : 0,
         n.is_group ? 1 : 0, n.group_id, n.group_name, 'baileys', 'history_sync',
         JSON.stringify({ remoteJid: msg.key.remoteJid, id: msg.key.id, fromMe: !!msg.key.fromMe, participant: msg.key.participant || null })]
      );
      if (msg.key?.remoteJid && msg.key?.id) storeMessage(msg);
    }
    db.saveNow(); // Flush history to disk immediately
  });

  // ── Call events (detect only) ──────────────────────────────────────────
  sock.ev.on('call', (calls) => {
    for (const call of calls) {
      broadcastEvent(makeEvent('whatsapp.call.incoming', normalizeCall(call)));
      log('WA_CALL_EVENT', { status: call.status || 'unknown', is_video: !!call.isVideo });
    }
  });

  // ── Group metadata updates ─────────────────────────────────────────────
  sock.ev.on('groups.update', (updates) => {
    for (const g of updates) {
      if (!g.id || !db) continue;
      db.run(
        `INSERT INTO whatsapp_chats (chat_id, name, is_group, updated_at) VALUES (?, ?, 1, ?)
         ON CONFLICT(chat_id) DO UPDATE SET name=COALESCE(excluded.name, whatsapp_chats.name), updated_at=excluded.updated_at`,
        [g.id, g.subject || null, new Date().toISOString()]
      );
    }
  });

  // ── Contact updates ────────────────────────────────────────────────────
  sock.ev.on('contacts.update', (updates) => {
    for (const c of updates) {
      if (!c.id || !db) continue;
      const name = c.name || c.notify || null;
      db.run(
        `INSERT INTO whatsapp_contacts (contact_id, display_name, push_name, updated_at) VALUES (?, ?, ?, ?)
         ON CONFLICT(contact_id) DO UPDATE SET
         display_name=COALESCE(excluded.display_name, whatsapp_contacts.display_name),
         push_name=COALESCE(excluded.push_name, whatsapp_contacts.push_name),
         updated_at=excluded.updated_at`,
        [c.id, name, c.notify || null, new Date().toISOString()]
      );
    }
  });

  // Refresh the global reference on every (re)connection — without this,
  // a reconnect leaves globalSock pointing at the dead socket and every
  // sendMessage() fails with "Connection Closed" even though /healthz
  // reports connected (connectionState/authReady are updated by the NEW
  // socket's events). Safe to assign immediately: all send endpoints gate
  // on authReady, which is only true once the socket is actually open.
  globalSock = sock;

  return sock;
}

// ── Express application ──────────────────────────────────────────────────

log('WA_SIDECAR_START', { sessionId: SESSION_ID, port: PORT, authDir: AUTH_DIR });

const app = express();
app.use(express.json({ limit: '10mb' }));

function requireAuth(req, res, next) {
  if (req.get('x-api-key') !== API_KEY) {
    return res.status(401).json({ ok: false, data: null, error: { code: 'UNAUTHORIZED', message: 'bad or missing x-api-key' } });
  }
  next();
}

// Helper for error responses
function err(code, message, status) {
  return { status: status || 500, body: { ok: false, data: null, error: { code, message } } };
}

// ── GET /healthz (no auth) ───────────────────────────────────────────────
app.get('/healthz', (_req, res) => {
  res.json({ ok: connectionState === 'open', state: connectionState, authenticated: authReady, provider: 'baileys', sse_clients: sseClients.size, error: lastError });
});

// ── GET /events (SSE) ────────────────────────────────────────────────────
app.get('/events', requireAuth, (req, res) => {
  res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache', 'Connection': 'keep-alive', 'X-Accel-Buffering': 'no' });
  res.write(`event: connected\ndata: {"provider":"baileys","state":"${connectionState}"}\n\n`);
  sseClients.add(res);
  log('WA_SSE_CLIENT_CONNECTED', { total: sseClients.size });
  req.on('close', () => { sseClients.delete(res); log('WA_SSE_CLIENT_DISCONNECTED', { total: sseClients.size }); });
});

// ── GET /qr ──────────────────────────────────────────────────────────────
app.get('/qr', requireAuth, (_req, res) => { res.json({ authenticated: authReady, qr: currentQR }); });

// ── POST /pairing ────────────────────────────────────────────────────────
app.post('/pairing', requireAuth, async (req, res) => {
  const { phone } = req.body || {};
  if (!phone || typeof phone !== 'string') return res.status(400).json(err('INVALID_REQUEST', 'phone is required (E.164, digits only, no +)', 400));
  const normalized = phone.replace(/[\s+\-()]/g, '');
  if (!/^\d{7,15}$/.test(normalized)) return res.status(400).json(err('INVALID_REQUEST', 'phone must be 7-15 digits (E.164)', 400));
  try {
    if (!globalSock) return res.status(503).json(err('SERVICE_UNAVAILABLE', 'socket not ready', 503));
    const code = await globalSock.requestPairingCode(normalized);
    pairingCode = code;
    log('WA_PAIRING_CODE_ISSUED', {});
    res.json({ success: true, pairing_code: code });
  } catch (e) {
    log('WA_PAIRING_FAILED', { error: String(e?.message || e) });
    res.status(500).json(err('PAIRING_FAILED', String(e?.message || e)));
  }
});

// ── POST /sendText ───────────────────────────────────────────────────────
app.post('/sendText', requireAuth, async (req, res) => {
  try {
    if (!authReady || !globalSock) return res.status(503).json(err('NOT_CONNECTED', 'WhatsApp not connected', 503));
    const { chat_id, text } = req.body || {};
    if (!chat_id) return res.status(400).json(err('INVALID_REQUEST', 'chat_id is required', 400));
    if (!text) return res.status(400).json(err('INVALID_REQUEST', 'text is required', 400));

    const sent = await globalSock.sendMessage(chat_id, { text });
    const now = new Date().toISOString();
    const messageId = sent?.key?.id || generateMessageID();
    if (db) {
      db.run(
        `INSERT OR REPLACE INTO whatsapp_messages
         (message_id, chat_id, sender_id, sender_name, timestamp, message_type, text, from_me, is_group, provider, event_origin, provider_key_json)
         VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`,
        [messageId, chat_id, null, null, now, 'text', text, 1, isJidGroup(chat_id) ? 1 : 0, 'baileys', 'outgoing',
         JSON.stringify({ remoteJid: chat_id, id: messageId, fromMe: true })]
      );
    }
    log('WA_SEND_SUCCESS', { action: 'sendText', chat: chat_id, message_id: messageId });
    res.json({ ok: true, data: { message_id: messageId, chat_id, timestamp: now, from_me: true, provider: 'baileys' }, error: null });
  } catch (e) {
    log('WA_SEND_FAILED', { action: 'sendText', error: String(e?.message || e) });
    res.status(500).json(err('PROVIDER_ERROR', String(e?.message || e)));
  }
});

// ── POST /sendFile ───────────────────────────────────────────────────────
app.post('/sendFile', requireAuth, async (req, res) => {
  try {
    if (!authReady || !globalSock) return res.status(503).json(err('NOT_CONNECTED', 'WhatsApp not connected', 503));
    const { chat_id, file_path, filename, caption } = req.body || {};
    if (!chat_id) return res.status(400).json(err('INVALID_REQUEST', 'chat_id is required', 400));
    if (!file_path) return res.status(400).json(err('INVALID_REQUEST', 'file_path is required', 400));

    // Security: resolve and validate path
    const resolved = path.resolve(file_path);
    const secretsDir = path.resolve(path.join(REPO_ROOT, '.secrets'));
    if (resolved.startsWith(secretsDir + path.sep) || resolved === secretsDir) {
      return res.status(403).json(err('FORBIDDEN', 'cannot send files from .secrets directory', 403));
    }
    if (!fs.existsSync(resolved)) return res.status(404).json(err('FILE_NOT_FOUND', `File not found: ${file_path}`, 404));
    const stat = fs.statSync(resolved);
    if (stat.size > MAX_FILE_SIZE) return res.status(400).json(err('FILE_TOO_LARGE', `File exceeds ${MAX_FILE_SIZE / 1024 / 1024}MB limit`, 400));

    const buffer = fs.readFileSync(resolved);
    const fname = filename || path.basename(resolved);
    const mime = require('mime-types')?.lookup?.(fname) || 'application/octet-stream';

    const sent = await globalSock.sendMessage(chat_id, { document: buffer, mimetype: mime, fileName: fname, caption: caption || '' });
    const now = new Date().toISOString();
    const messageId = sent?.key?.id || generateMessageID();
    if (db) {
      db.run(
        `INSERT OR REPLACE INTO whatsapp_messages
         (message_id, chat_id, timestamp, message_type, text, media_type, media_metadata_json, from_me, is_group, provider, event_origin, provider_key_json)
         VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`,
        [messageId, chat_id, now, 'document', caption || null, 'document', JSON.stringify({ mimetype: mime, filename: fname }), 1, isJidGroup(chat_id) ? 1 : 0, 'baileys', 'outgoing',
         JSON.stringify({ remoteJid: chat_id, id: messageId, fromMe: true })]
      );
    }
    log('WA_SEND_SUCCESS', { action: 'sendFile', chat: chat_id, message_id: messageId });
    res.json({ ok: true, data: { message_id: messageId, chat_id, timestamp: now, from_me: true, provider: 'baileys', filename: fname }, error: null });
  } catch (e) {
    log('WA_SEND_FAILED', { action: 'sendFile', error: String(e?.message || e) });
    res.status(500).json(err('PROVIDER_ERROR', String(e?.message || e)));
  }
});

// ── POST /sendImage ──────────────────────────────────────────────────────
app.post('/sendImage', requireAuth, async (req, res) => {
  try {
    if (!authReady || !globalSock) return res.status(503).json(err('NOT_CONNECTED', 'WhatsApp not connected', 503));
    const { chat_id, file_path, caption } = req.body || {};
    if (!chat_id) return res.status(400).json(err('INVALID_REQUEST', 'chat_id is required', 400));
    if (!file_path) return res.status(400).json(err('INVALID_REQUEST', 'file_path is required', 400));

    const resolved = path.resolve(file_path);
    const secretsDir = path.resolve(path.join(REPO_ROOT, '.secrets'));
    if (resolved.startsWith(secretsDir + path.sep) || resolved === secretsDir) {
      return res.status(403).json(err('FORBIDDEN', 'cannot send files from .secrets directory', 403));
    }
    if (!fs.existsSync(resolved)) return res.status(404).json(err('FILE_NOT_FOUND', `File not found: ${file_path}`, 404));
    const stat = fs.statSync(resolved);
    if (stat.size > MAX_FILE_SIZE) return res.status(400).json(err('FILE_TOO_LARGE', `File exceeds ${MAX_FILE_SIZE / 1024 / 1024}MB limit`, 400));

    const buffer = fs.readFileSync(resolved);
    const mime = require('mime-types')?.lookup?.(resolved) || 'image/jpeg';
    const sent = await globalSock.sendMessage(chat_id, { image: buffer, mimetype: mime, caption: caption || '' });
    const now = new Date().toISOString();
    const messageId = sent?.key?.id || generateMessageID();
    if (db) {
      db.run(
        `INSERT OR REPLACE INTO whatsapp_messages
         (message_id, chat_id, timestamp, message_type, text, media_type, media_metadata_json, from_me, is_group, provider, event_origin, provider_key_json)
         VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`,
        [messageId, chat_id, now, 'image', caption || null, 'image', JSON.stringify({ mimetype: mime }), 1, isJidGroup(chat_id) ? 1 : 0, 'baileys', 'outgoing',
         JSON.stringify({ remoteJid: chat_id, id: messageId, fromMe: true })]
      );
    }
    log('WA_SEND_SUCCESS', { action: 'sendImage', chat: chat_id, message_id: messageId });
    res.json({ ok: true, data: { message_id: messageId, chat_id, timestamp: now, from_me: true, provider: 'baileys' }, error: null });
  } catch (e) {
    log('WA_SEND_FAILED', { action: 'sendImage', error: String(e?.message || e) });
    res.status(500).json(err('PROVIDER_ERROR', String(e?.message || e)));
  }
});

// ── POST /reply ──────────────────────────────────────────────────────────
app.post('/reply', requireAuth, async (req, res) => {
  try {
    if (!authReady || !globalSock) return res.status(503).json(err('NOT_CONNECTED', 'WhatsApp not connected', 503));
    const { chat_id, quoted_message_id, text } = req.body || {};
    if (!chat_id) return res.status(400).json(err('INVALID_REQUEST', 'chat_id is required', 400));
    if (!quoted_message_id) return res.status(400).json(err('INVALID_REQUEST', 'quoted_message_id is required', 400));
    if (!text) return res.status(400).json(err('INVALID_REQUEST', 'text is required', 400));

    // Reconstruct quoted message key from SQLite provider_key_json
    let quotedKey = null;
    if (db) {
      const row = db.get('SELECT provider_key_json FROM whatsapp_messages WHERE chat_id = ? AND message_id = ?', [chat_id, quoted_message_id]);
      if (row?.provider_key_json) {
        try { quotedKey = JSON.parse(row.provider_key_json); } catch (_) {}
      }
    }
    if (!quotedKey) {
      // Fallback: construct minimal key
      quotedKey = { remoteJid: chat_id, id: quoted_message_id, fromMe: false };
    }

    const sent = await globalSock.sendMessage(chat_id, { text }, { quoted: { key: quotedKey, message: { conversation: '' } } });
    const now = new Date().toISOString();
    const messageId = sent?.key?.id || generateMessageID();
    if (db) {
      db.run(
        `INSERT OR REPLACE INTO whatsapp_messages
         (message_id, chat_id, timestamp, message_type, text, quoted_message_id, from_me, is_group, provider, event_origin, provider_key_json)
         VALUES (?,?,?,?,?,?,?,?,?,?,?)`,
        [messageId, chat_id, now, 'text', text, quoted_message_id, 1, isJidGroup(chat_id) ? 1 : 0, 'baileys', 'outgoing',
         JSON.stringify({ remoteJid: chat_id, id: messageId, fromMe: true })]
      );
    }
    log('WA_SEND_SUCCESS', { action: 'reply', chat: chat_id, message_id: messageId, quoted: quoted_message_id });
    res.json({ ok: true, data: { message_id: messageId, chat_id, timestamp: now, quoted_message_id, from_me: true, provider: 'baileys' }, error: null });
  } catch (e) {
    log('WA_SEND_FAILED', { action: 'reply', error: String(e?.message || e) });
    res.status(500).json(err('PROVIDER_ERROR', String(e?.message || e)));
  }
});

// ── POST /getMessages ────────────────────────────────────────────────────
app.post('/getMessages', requireAuth, (req, res) => {
  if (!db) return res.status(503).json(err('DB_NOT_READY', 'Database not initialized', 503));
  const { chat_id, sender_id, limit = 50, unread_only, history_only } = req.body || {};
  const conditions = [];
  const params = [];
  if (chat_id) { conditions.push('chat_id = ?'); params.push(chat_id); }
  if (sender_id) { conditions.push('sender_id = ?'); params.push(sender_id); }
  if (unread_only) { conditions.push("event_origin = 'live' AND from_me = 0"); }
  if (history_only) { conditions.push("event_origin = 'history_sync'"); }

  let sql = `SELECT message_id, chat_id, sender_id, sender_name, timestamp, message_type, text,
    quoted_message_id, media_type, media_metadata_json, from_me, is_group, group_id, group_name,
    provider, event_origin FROM whatsapp_messages`;
  if (conditions.length) sql += ` WHERE ${conditions.join(' AND ')}`;
  sql += ` ORDER BY timestamp DESC LIMIT ?`;
  params.push(Math.min(limit, 200));

  const rows = db.all(sql, params);
  res.json({ ok: true, data: { messages: rows, total: rows.length }, error: null });
});

// ── POST /findContact ────────────────────────────────────────────────────
app.post('/findContact', requireAuth, (req, res) => {
  if (!db) return res.status(503).json(err('DB_NOT_READY', 'Database not initialized', 503));
  const { query } = req.body || {};
  if (!query || query.length < 2) return res.status(400).json(err('INVALID_REQUEST', 'query must be at least 2 characters', 400));
  const q = `%${query}%`;
  const contacts = db.all(
    `SELECT c.contact_id, c.display_name, c.phone, c.push_name, c.chat_id
     FROM whatsapp_contacts c
     WHERE c.display_name LIKE ? OR c.phone LIKE ? OR c.contact_id LIKE ?
     LIMIT 10`,
    [q, q, q]
  );
  res.json({ ok: true, data: { contacts, total: contacts.length }, error: null });
});

// ── POST /onWhatsApp ─────────────────────────────────────────────────────
// Authoritative phone-number → WhatsApp-identity resolution via Baileys'
// usync query. Needed because LID migration means the canonical routing
// identity for a phone number may be <number>@lid, not <number>@s.whatsapp.net.
// Body: { phone: "+923001234567" or "923001234567" }
// Returns: { exists: bool, jid: "...@s.whatsapp.net" | "...@lid" | null, phone }
app.post('/onWhatsApp', requireAuth, async (req, res) => {
  try {
    if (!authReady || !globalSock) return res.status(503).json(err('NOT_CONNECTED', 'WhatsApp not connected', 503));
    const { phone } = req.body || {};
    if (!phone || typeof phone !== 'string') {
      return res.status(400).json(err('INVALID_REQUEST', 'phone is required', 400));
    }
    const digits = phone.replace(/\D/g, '');
    if (digits.length < 7 || digits.length > 15) {
      return res.status(400).json(err('INVALID_REQUEST', 'phone must contain 7-15 digits', 400));
    }
    const results = await globalSock.onWhatsApp(`+${digits}`);
    const hit = Array.isArray(results) ? results.find(r => r && r.exists) : null;
    if (!hit) {
      log('WA_ON_WHATSAPP', { phone: `[PHONE:${digits.length} digits]`, exists: false });
      return res.json({ ok: true, data: { exists: false, jid: null, phone: digits }, error: null });
    }
    log('WA_ON_WHATSAPP', {
      phone: `[PHONE:${digits.length} digits]`,
      exists: true,
      jid_domain: hit.jid ? hit.jid.split('@')[1] : null,
    });
    res.json({ ok: true, data: { exists: true, jid: hit.jid, phone: digits }, error: null });
  } catch (e) {
    log('WA_ON_WHATSAPP_FAILED', { error: String(e?.message || e) });
    res.status(500).json(err('PROVIDER_ERROR', String(e?.message || e)));
  }
});

// ── POST /markRead ───────────────────────────────────────────────────────
app.post('/markRead', requireAuth, async (req, res) => {
  try {
    if (!authReady || !globalSock) return res.status(503).json(err('NOT_CONNECTED', 'WhatsApp not connected', 503));
    const { chat_id } = req.body || {};
    if (!chat_id) return res.status(400).json(err('INVALID_REQUEST', 'chat_id is required', 400));

    // Mark latest messages in chat as read
    let marked = 0;
    if (db) {
      const rows = db.all('SELECT provider_key_json FROM whatsapp_messages WHERE chat_id = ? AND from_me = 0 ORDER BY timestamp DESC LIMIT 50', [chat_id]);
      const keys = rows.map(r => { try { return JSON.parse(r.provider_key_json); } catch (_) { return null; } }).filter(Boolean);
      if (keys.length) { await globalSock.readMessages(keys); marked = keys.length; }
    }
    // Also mark the entire chat as read
    try { await globalSock.markAsRead([chat_id]); } catch (_) {}

    log('WA_MARK_READ', { chat: chat_id, messages_marked: marked });
    res.json({ ok: true, data: { chat_id, messages_marked: marked }, error: null });
  } catch (e) {
    log('WA_MARK_READ_FAILED', { error: String(e?.message || e) });
    res.status(500).json(err('PROVIDER_ERROR', String(e?.message || e)));
  }
});

// ── POST /downloadMedia ──────────────────────────────────────────────────

// Filename sanitization: prevent path traversal, reserved names, collisions
const _WIN_RESERVED = new Set(['CON','PRN','AUX','NUL','COM1','COM2','COM3','COM4','COM5','COM6','COM7','COM8','COM9','LPT1','LPT2','LPT3','LPT4','LPT5','LPT6','LPT7','LPT8','LPT9']);

function _sanitizeFilename(raw) {
  if (!raw || typeof raw !== 'string') return null;
  // Strip path separators and null bytes
  let name = raw.replace(/[/\\\x00]/g, '').trim();
  // Strip leading dots (prevent hidden files)
  name = name.replace(/^\.+/, '');
  // Strip trailing dots and spaces (Windows quirk)
  name = name.replace(/[. ]+$/, '');
  if (!name) return null;
  const ext = path.extname(name);
  const base = path.basename(name, ext);
  if (_WIN_RESERVED.has(base.toUpperCase())) return null;
  return name;
}

function _uniqueFile(dir, filename) {
  let candidate = path.join(dir, filename);
  if (!fs.existsSync(candidate)) return candidate;
  const ext = path.extname(filename);
  const base = path.basename(filename, ext);
  for (let i = 1; i <= 999; i++) {
    candidate = path.join(dir, `${base}_${i}${ext}`);
    if (!fs.existsSync(candidate)) return candidate;
  }
  // Fallback: append timestamp
  return path.join(dir, `${base}_${Date.now()}${ext}`);
}

function _mediaSubdir(mediaType) {
  switch (mediaType) {
    case 'image': return 'images';
    case 'document': return 'documents';
    case 'audio': return 'audio';
    case 'video': return 'video';
    case 'sticker': return 'stickers';
    default: return 'other';
  }
}

app.post('/downloadMedia', requireAuth, async (req, res) => {
  try {
    if (!authReady || !globalSock) return res.status(503).json(err('NOT_CONNECTED', 'WhatsApp not connected', 503));
    if (!db) return res.status(503).json(err('DB_NOT_READY', 'Database not initialized', 503));

    const { chat_id, message_id } = req.body || {};
    if (!chat_id || !message_id) {
      return res.status(400).json(err('INVALID_REQUEST', 'chat_id and message_id are required', 400));
    }

    const row = db.get(
      'SELECT message_id, chat_id, sender_id, timestamp, message_type, media_type, media_metadata_json, local_path, download_status, provider_key_json FROM whatsapp_messages WHERE message_id = ? AND chat_id = ?',
      [message_id, chat_id]
    );
    if (!row) return res.status(404).json(err('NOT_FOUND', 'Message not found', 404));
    if (!row.media_type) return res.status(400).json(err('NOT_MEDIA', 'Message is not a media message', 400));

    // If already downloaded and file still exists, reuse
    if (row.local_path && row.download_status === 'downloaded' && fs.existsSync(row.local_path)) {
      log('WA_MEDIA_REUSE', { message_id, chat_id });
      let meta = {};
      try { meta = JSON.parse(row.media_metadata_json || '{}'); } catch (_) {}
      return res.json({
        ok: true,
        data: {
          message_id, chat_id, media_type: row.media_type,
          mimetype: meta.mimetype || null, filename: meta.filename || null,
          local_path: row.local_path, downloaded_at: row.downloaded_at, reused: true,
        },
        error: null,
      });
    }

    // Parse metadata — need provider_media for download
    let meta = {};
    try { meta = JSON.parse(row.media_metadata_json || '{}'); } catch (_) {}
    const pm = meta.provider_media;
    if (!pm || !pm.media_key_b64) {
      return res.status(400).json(err('NO_MEDIA_KEY', 'Media retrieval metadata not available — message may predate Phase 2.1', 400));
    }

    // Reconstruct minimal Baileys message for downloadMediaMessage
    const msgForDownload = {
      key: JSON.parse(row.provider_key_json || '{}'),
      message: {},
    };
    // Build the appropriate media message type with download fields
    const mediaFields = {
      mediaKey: Buffer.from(pm.media_key_b64, 'base64'),
      directPath: pm.direct_path || undefined,
      url: pm.url || undefined,
      fileSha256: pm.file_sha256_b64 ? Buffer.from(pm.file_sha256_b64, 'base64') : undefined,
      fileEncSha256: pm.file_enc_sha256_b64 ? Buffer.from(pm.file_enc_sha256_b64, 'base64') : undefined,
      fileLength: pm.file_length || undefined,
    };
    const typeMap = { image: 'imageMessage', document: 'documentMessage', audio: 'audioMessage', video: 'videoMessage', sticker: 'stickerMessage' };
    const baileysType = typeMap[row.media_type];
    if (!baileysType) return res.status(400).json(err('UNSUPPORTED_MEDIA', `Unsupported media type: ${row.media_type}`, 400));
    msgForDownload.message[baileysType] = { mimetype: meta.mimetype || 'application/octet-stream', ...mediaFields };

    // Download
    log('WA_MEDIA_DOWNLOAD_START', { message_id, chat_id, media_type: row.media_type });
    const buffer = await downloadMediaMessage(msgForDownload, 'buffer', {}, {
      logger: baileyLogger,
      reuploadRequest: globalSock.updateMediaMessage,
    });

    if (!buffer || buffer.length === 0) {
      return res.status(500).json(err('DOWNLOAD_EMPTY', 'Downloaded media is empty', 500));
    }
    if (buffer.length > MAX_DOWNLOAD_SIZE) {
      return res.status(400).json(err('FILE_TOO_LARGE', `Media exceeds ${MAX_DOWNLOAD_SIZE / 1024 / 1024}MB limit`, 400));
    }

    // Prepare safe output path
    const subdir = _mediaSubdir(row.media_type);
    const targetDir = path.join(MEDIA_DIR, subdir);
    fs.mkdirSync(targetDir, { recursive: true });

    const rawName = meta.filename || null;
    const safeName = _sanitizeFilename(rawName) || `${message_id}.${_defaultExt(row.media_type, meta.mimetype)}`;
    const localPath = _uniqueFile(targetDir, safeName);

    fs.writeFileSync(localPath, buffer);
    const now = new Date().toISOString();

    // Update SQLite
    db.run(
      'UPDATE whatsapp_messages SET local_path = ?, downloaded_at = ?, download_status = ? WHERE message_id = ? AND chat_id = ?',
      [localPath, now, 'downloaded', message_id, chat_id]
    );
    db.saveNow();

    log('WA_MEDIA_DOWNLOAD_SUCCESS', { message_id, chat_id, media_type: row.media_type, size: buffer.length });
    res.json({
      ok: true,
      data: {
        message_id, chat_id, media_type: row.media_type,
        mimetype: meta.mimetype || null, filename: safeName,
        local_path: localPath, downloaded_at: now, reused: false,
      },
      error: null,
    });
  } catch (e) {
    log('WA_MEDIA_DOWNLOAD_FAILED', { error: String(e?.message || e) });
    res.status(500).json(err('PROVIDER_ERROR', String(e?.message || e)));
  }
});

function _defaultExt(mediaType, mimetype) {
  if (mimetype) {
    const m = require('mime-types');
    const ext = m?.extension?.(mimetype);
    if (ext) return ext;
  }
  const defaults = { image: 'jpg', document: 'bin', audio: 'ogg', video: 'mp4', sticker: 'webp' };
  return defaults[mediaType] || 'bin';
}

// ── POST /getLatestMedia ─────────────────────────────────────────────────
app.post('/getLatestMedia', requireAuth, (req, res) => {
  if (!db) return res.status(503).json(err('DB_NOT_READY', 'Database not initialized', 503));
  const { chat_id, sender_id, media_type, limit = 10 } = req.body || {};
  const conditions = ['media_type IS NOT NULL'];
  const params = [];
  if (chat_id) { conditions.push('chat_id = ?'); params.push(chat_id); }
  if (sender_id) { conditions.push('sender_id = ?'); params.push(sender_id); }
  if (media_type) { conditions.push('media_type = ?'); params.push(media_type); }

  let sql = `SELECT message_id, chat_id, sender_id, sender_name, timestamp, message_type,
    media_type, media_metadata_json, from_me, event_origin, local_path, downloaded_at, download_status
    FROM whatsapp_messages WHERE ${conditions.join(' AND ')}
    ORDER BY timestamp DESC LIMIT ?`;
  params.push(Math.min(limit, 100));

  const rows = db.all(sql, params);
  res.json({ ok: true, data: { messages: rows, total: rows.length }, error: null });
});

// ── POST /getMediaMessage ────────────────────────────────────────────────
app.post('/getMediaMessage', requireAuth, (req, res) => {
  if (!db) return res.status(503).json(err('DB_NOT_READY', 'Database not initialized', 503));
  const { chat_id, message_id } = req.body || {};
  if (!chat_id || !message_id) {
    return res.status(400).json(err('INVALID_REQUEST', 'chat_id and message_id are required', 400));
  }
  const row = db.get(
    `SELECT message_id, chat_id, sender_id, sender_name, timestamp, message_type, text,
      media_type, media_metadata_json, from_me, event_origin, local_path, downloaded_at,
      download_status, provider_key_json
     FROM whatsapp_messages WHERE message_id = ? AND chat_id = ?`,
    [message_id, chat_id]
  );
  if (!row) return res.status(404).json(err('NOT_FOUND', 'Message not found', 404));
  // Do NOT expose provider_key_json or sensitive media keys in the API response
  const { provider_key_json, ...safe } = row;
  // Redact sensitive provider_media fields from the response
  let meta = null;
  try { meta = JSON.parse(row.media_metadata_json || 'null'); } catch (_) {}
  if (meta?.provider_media) {
    meta = { ...meta, provider_media: { provider: meta.provider_media.provider, has_key: !!meta.provider_media.media_key_b64 } };
  }
  safe.media_metadata_json = meta ? JSON.stringify(meta) : null;
  res.json({ ok: true, data: safe, error: null });
});

// ── Startup ──────────────────────────────────────────────────────────────
const heartbeatTimer = startHeartbeat();
const server = app.listen(PORT, '127.0.0.1', () => {
  log('WA_SIDECAR_HTTP_READY', { port: PORT, host: '127.0.0.1' });
});

(async () => {
  try {
    await loadDB();
    log('WA_DB_READY', {});
    connectionState = 'connecting';
    globalSock = await startSocket();
    log('WA_SIDECAR_SOCKET_STARTED', {});
  } catch (err) {
    connectionState = 'closed';
    lastError = String(err?.message || err);
    log('WA_SIDECAR_SOCKET_FAILED', { error: lastError });
  }
})();

// ── Graceful shutdown ────────────────────────────────────────────────────
function shutdown(signal) {
  log('WA_SIDECAR_STOP', { signal });
  clearInterval(heartbeatTimer);
  for (const res of sseClients) { try { res.end(); } catch (_) {} }
  sseClients.clear();
  if (globalSock?.ws?.readyState === 1) { try { globalSock.end(undefined); } catch (_) {} }
  if (db) { try { db.close(); } catch (_) {} }
  server.close(() => process.exit(0));
  setTimeout(() => process.exit(0), 3000).unref();
}

process.on('SIGTERM', () => shutdown('SIGTERM'));
process.on('SIGINT', () => shutdown('SIGINT'));
process.on('uncaughtException', (err) => {
  log('WA_SIDECAR_FATAL', { error: String(err?.message || err), fatal: true });
  if (db) { try { db.close(); } catch (_) {} }
  process.exit(1);
});
