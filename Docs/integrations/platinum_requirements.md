# Platinum Tier Requirements
**Source:** Hackathon-0 PDF ("Personal AI Employee Hackathon 0: Building Autonomous FTEs in 2026")
**Section:** "Platinum Tier: Always-On Cloud + Local Executive (Production-ish AI Employee)"
**Estimated time:** 60+ hours
**Date extracted:** 2026-02-27

---

## Summary

Platinum Tier elevates the Gold Tier system by splitting responsibilities between a **Cloud Worker** (always-on, 24/7, draft-only) and a **Local Executive** (approvals, final actions). They coordinate through a **Git-synced vault** using file-based handoffs with a strict claim-by-move protocol.

---

## Platinum Acceptance Criteria (verbatim from PDF)

All Gold requirements **plus**:

### PT-1: Always-On Cloud Agent
> "Run the AI Employee on Cloud 24/7 (always-on watchers + orchestrator + health monitoring). You can deploy a Cloud VM (Oracle/AWS/etc.) — Oracle Cloud Free VMs can be used for this (subject to limits/availability)."

**Acceptance criteria:**
- [ ] Cloud worker runs 24/7 with PM2 (or systemd) process management
- [ ] Health monitoring in place (SERVICE_READY signal, `/tmp/<service>.ready` touchfile)
- [ ] Oracle Free Tier compatible deployment documented

### PT-2: Work-Zone Specialization (Domain Ownership)
> "Cloud owns: Email triage + draft replies + social post drafts/scheduling (draft-only; requires Local approval before send/post). Local owns: approvals, WhatsApp session, payments/banking, and final 'send/post' actions."

**Acceptance criteria:**
- [ ] Cloud never sends email, never posts social, never processes payments
- [ ] Cloud only writes draft plans + approval request files
- [ ] Local executes all irreversible external actions

### PT-3: Delegation via Synced Vault
> "Agents communicate by writing files into: /Needs_Action/<domain>/, /Plans/<domain>/, /Pending_Approval/<domain>/"

**Vault folders required:**
- `Needs_Action/email/`
- `Needs_Action/social/`
- `Needs_Action/accounting/`
- `In_Progress/cloud/`
- `In_Progress/local/`
- `Pending_Approval/email/`
- `Pending_Approval/social/`
- `Pending_Approval/accounting/`
- `Updates/cloud/` (cloud writes summaries here)
- `Updates/cloud/processed/` (after Local merges)
- `Approved/` (existing)
- `Rejected/` (existing)
- `Done/` (existing)

**Acceptance criteria:**
- [ ] Claim-by-move rule enforced: first agent to move `Needs_Action/<domain>/<item>` → `In_Progress/<agent>/` owns it
- [ ] Single-writer rule: only Local writes to `Dashboard.md`
- [ ] Cloud writes status to `Updates/cloud/`, Local merges into Dashboard

### PT-4: Git-Based Vault Sync (Phase 1)
> "For Vault sync (Phase 1) use Git (recommended) or Syncthing."

**Acceptance criteria:**
- [ ] Cloud worker: `git pull` before scanning, `git commit + push` after writing approvals/updates
- [ ] Local: `git pull` to receive cloud artifacts before processing
- [ ] Secrets (`.env`, tokens, sessions) are NEVER in git

### PT-5: Security — Secrets Never Sync
> "Vault sync includes only markdown/state. Secrets never sync (.env, tokens, WhatsApp sessions, banking creds). So Cloud never stores or uses WhatsApp sessions, banking credentials, or payment tokens."

**Acceptance criteria:**
- [ ] `.secrets/` directory gitignored
- [ ] `.env` files gitignored
- [ ] Cloud worker code never reads secret files (tokens, sessions, banking creds)
- [ ] Cloud worker tests confirm no secret writes

### PT-6: Odoo on Cloud (24/7)
> "Deploy Odoo Community on a Cloud VM (24/7) with HTTPS, backups, and health monitoring; integrate Cloud Agent with Odoo via MCP for draft-only accounting actions and Local approval for posting invoices/payments."

**Acceptance criteria:**
- [ ] Deployment documented in `Docs/platinum_deploy_oracle.md`
- [ ] Cloud can query Odoo via MCP (read-only + draft)
- [ ] Invoice/payment posting requires Local approval

### PT-7: Optional A2A Upgrade (Phase 2)
> "Replace some file handoffs with direct A2A messages later, while keeping the vault as the audit record."

**Status:** Out of scope for initial Platinum implementation. Documented as future work.

---

## PT-8: Platinum Demo (Minimum Passing Gate) ⭐

> "Email arrives while Local is offline → Cloud drafts reply + writes approval file → when Local returns, user approves → Local executes send via MCP → logs → moves task to /Done."

**Step-by-step:**
1. Stop all local services (`pm2 stop all` or kill watchers)
2. Send an email to the monitored inbox
3. Cloud worker (on VM) detects the email via Gmail watcher
4. Cloud worker drafts a reply and creates `Pending_Approval/email/plan__email__<slug>__<ts>.md`
5. Cloud worker commits + pushes to git
6. Restore local services; local pulls (`git pull`)
7. User reviews and renames file to signal approval (or uses `local_merge_updates.py --approve`)
8. Local executor reads the approved plan and executes email send via MCP
9. Execution logged to `Logs/YYYY-MM-DD.json`
10. Item moved to `Done/`

**See:** `Docs/platinum_demo_script.md` for full judge walkthrough.

---

## Implementation Files

| File | Purpose |
|------|---------|
| `src/personal_ai_employee/skills/platinum/cloud_worker_orchestrator.py` | Cloud-side draft + approval creation |
| `src/personal_ai_employee/skills/platinum/local_merge_updates.py` | Local-side Dashboard updater |
| `src/personal_ai_employee/core/git_sync.py` | Git pull/commit/push helpers |
| `scripts/cloud_worker_orchestrator.py` | Wrapper for cloud skill |
| `scripts/local_merge_updates.py` | Wrapper for local merge skill |
| `scripts/local_sync_pull.py` | Quick git pull helper |
| `scripts/demo/platinum_gate.sh` | Demo gate validation script |
| `ecosystem.platinum.cloud.config.cjs` | PM2 config for cloud deployment |
| `Docs/platinum_vault_contract.md` | Vault folder + naming rules |
| `Docs/platinum_demo_script.md` | Step-by-step judge demo |
| `Docs/platinum_deploy_oracle.md` | Oracle Free VM deployment guide |

---

## Non-Goals (Platinum Phase 1)

- A2A (Agent-to-Agent) direct messaging — Phase 2
- WhatsApp on cloud — Local only (session security)
- Banking/payment automation on cloud — Local only
- Multi-cloud or Kubernetes deployment
