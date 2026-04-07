# Platinum Vault Contract
**Version:** 1.0
**Date:** 2026-02-27
**Tier:** Platinum

---

## 1. Overview

The vault is the **shared state bus** between the Cloud Worker and Local Executive. All coordination happens via Markdown files in specific folders. This document defines the rules every agent MUST follow.

---

## 2. Folder Structure

```
vault/
├── Needs_Action/           ← Intake queue (watchers write here)
│   ├── email/              ← Unprocessed email items
│   ├── social/             ← Unprocessed social items
│   └── accounting/         ← Unprocessed accounting items
│
├── In_Progress/            ← Claimed work (atomic move from Needs_Action)
│   ├── cloud/              ← Cloud Worker currently owns these
│   └── local/              ← Local Executive currently owns these
│
├── Pending_Approval/       ← Drafted plans awaiting human approval
│   ├── email/
│   ├── social/
│   └── accounting/
│
├── Approved/               ← Human has approved; Local executes
├── Rejected/               ← Human rejected; Cloud notified via update
├── Done/                   ← Completed tasks (terminal state)
│
├── Updates/
│   ├── cloud/              ← Cloud writes status summaries (NOT Dashboard.md)
│   └── cloud/processed/    ← After Local merges, moved here
│
├── Plans/                  ← Full execution plans (reference)
└── Dashboard.md            ← SINGLE WRITER: Local only
```

---

## 3. File Naming Conventions

### 3.1 Intake Items (Needs_Action)
```
<source>__<channel>__<YYYYMMDD-HHMM>__<slug>.md

Examples:
  inbox__gmail__20260227-1430__client_reply_request.md
  social__linkedin__20260227-0900__post_draft_needed.md
  accounting__odoo__20260227-1200__invoice_review.md
```

### 3.2 In_Progress Claims
When an agent claims an item, it moves the file and renames it:
```
<original-filename>   (kept as-is, moved to In_Progress/<agent>/)

Examples:
  In_Progress/cloud/inbox__gmail__20260227-1430__client_reply_request.md
  In_Progress/local/inbox__gmail__20260227-1430__client_reply_request.md
```

### 3.3 Approval Plans (Pending_Approval)
```
plan__<domain>__<slug>__<YYYYMMDD-HHMMSS>.md

Examples:
  Pending_Approval/email/plan__email__client_reply__20260227-143022.md
  Pending_Approval/social/plan__social__linkedin_post__20260227-090015.md
  Pending_Approval/accounting/plan__accounting__invoice_123__20260227-120045.md
```

### 3.4 Cloud Updates (Updates/cloud)
```
update__<YYYYMMDD-HHMMSS>.md

Example:
  Updates/cloud/update__20260227-143100.md
```

### 3.5 Processed Cloud Updates
```
Updates/cloud/processed/update__<YYYYMMDD-HHMMSS>.md
  (moved from Updates/cloud/ after Local merges into Dashboard.md)
```

---

## 4. Claim-by-Move Rule

**Rule:** The first agent to atomically move a file from `Needs_Action/<domain>/` to `In_Progress/<agent>/` **owns that item**. All other agents MUST ignore files already in `In_Progress/`.

**Implementation:**
```python
# Claim: move file atomically (Python shutil.move is atomic on same filesystem)
src = Path("Needs_Action/email/inbox__gmail__20260227-1430__item.md")
dst = Path("In_Progress/cloud/inbox__gmail__20260227-1430__item.md")
shutil.move(str(src), str(dst))  # If this succeeds, agent owns the file
```

**On failure (file already moved):** Agent receives FileNotFoundError, logs "already claimed", moves on.

**Max in-flight:** Cloud worker halts new claims if `len(list(In_Progress/cloud/)) > MAX_PENDING_APPROVALS`.

---

## 5. Who Writes What

| Agent | CAN write to | CANNOT write to |
|-------|-------------|-----------------|
| **Cloud Worker** | `Needs_Action/<domain>/` (intake only) | `Dashboard.md` |
| **Cloud Worker** | `In_Progress/cloud/` (claimed items) | `Approved/` |
| **Cloud Worker** | `Pending_Approval/<domain>/` (drafts) | `Done/` |
| **Cloud Worker** | `Updates/cloud/` (status notes) | Any file outside vault except its own git |
| **Local Executive** | `Dashboard.md` (SOLE writer) | `In_Progress/cloud/` |
| **Local Executive** | `In_Progress/local/` (claimed items) | `Updates/cloud/` |
| **Local Executive** | `Approved/`, `Rejected/`, `Done/` | — |
| **Local Executive** | `Updates/cloud/processed/` (after merge) | — |
| **Watchers** | `Needs_Action/<domain>/` (intake) | Everywhere else |

---

## 6. Dashboard Single-Writer Policy

`Dashboard.md` is the **single source of truth** for system status. Only the **Local Executive** (via `local_merge_updates.py`) may write to it.

**Enforcement:**
- Cloud worker code MUST NOT open or write `Dashboard.md`
- Cloud worker MUST NOT pass `Dashboard.md` as a target path
- Test `tests/test_platinum_no_dashboard_write_cloud.py` validates this statically

**Cloud-to-Dashboard flow:**
```
Cloud writes → Updates/cloud/update__<ts>.md
                    ↓ git push
Local pulls → git pull
Local merges → local_merge_updates.py reads Updates/cloud/*.md
                    ↓
             Updates Dashboard.md "Cloud Updates" section
                    ↓
             Moves processed files to Updates/cloud/processed/
```

---

## 7. Approval Workflow (Cloud → Human → Local)

```
1. Cloud: Claim item (Needs_Action → In_Progress/cloud/)
2. Cloud: Generate draft plan (Pending_Approval/<domain>/plan__*.md)
3. Cloud: Write status (Updates/cloud/update__*.md)
4. Cloud: git commit + push
5. Human: Review Pending_Approval/<domain>/plan__*.md
6. Human: Copy/move plan to Approved/ (signals approval)
           OR move to Rejected/ (signals rejection)
7. Local: git pull
8. Local: Detect new file in Approved/
9. Local: Execute plan via existing executor MCP skill
10. Local: Log to Logs/YYYY-MM-DD.json
11. Local: Move item to Done/
12. Local: Update Dashboard.md
```

---

## 8. Git Sync Protocol

### Cloud side (after writing approvals/updates):
```bash
git pull origin main       # avoid conflicts
git add Pending_Approval/ Updates/cloud/ In_Progress/
git commit -m "platinum(cloud): drafted approvals + updates [ts]"
git push origin main
```

### Local side (before processing approvals):
```bash
git pull origin main       # receive cloud artifacts
# process Approved/ files → execute → Done/
git add Approved/ Rejected/ Done/ Dashboard.md Logs/
git commit -m "platinum(local): executed approvals + updated dashboard [ts]"
git push origin main
```

---

## 9. Safety Invariants

1. **No secrets in vault.** Files in `Needs_Action/`, `Pending_Approval/`, `Updates/` must NEVER contain API keys, passwords, tokens, or session data.
2. **Draft plans are reversible.** Cloud creates draft plans only. No irreversible action is taken by cloud.
3. **Approval flood protection.** Cloud halts if `In_Progress/cloud/` count ≥ `MAX_PENDING_APPROVALS` (default: 10).
4. **Idempotent merge.** `local_merge_updates.py` is safe to run multiple times; processed updates are moved to `processed/`.
5. **Atomic claim.** `shutil.move()` on the same filesystem is effectively atomic; no locking needed.
