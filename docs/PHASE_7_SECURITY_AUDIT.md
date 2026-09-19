# Phase 7 — Security & Permissions Architecture Audit

**Date:** September 17, 2026
**Scope:** Complete audit of existing security, permission, approval, emergency-stop,
audit-logging, secret-handling, and tool-metadata code across Phases 1–6.

---

## 1. Existing Safeguards

### 1.1 PermissionEngine (`src/agent/permissions/__init__.py`)

The central permission gate, already enforced **outside the LLM** at the tool execution
boundary (`ToolRegistry.execute`).

| Feature | Status | Notes |
|---|---|---|
| `PermissionLevel` enum (4 tiers) | ✅ Built | `OBSERVE=0`, `LOCAL_ACTION=1`, `EXTERNAL_ACTION=2`, `HIGH_RISK=3` |
| `AutonomyMode` (3 modes) | ✅ Built | `OBSERVE`, `ASSIST`, `AUTONOMOUS` |
| Path-based secret detection | ✅ Built | 20+ patterns: `.env`, `id_rsa`, `credentials.json`, `wallet.dat`, etc. |
| Directory traversal blocking | ✅ Built | Rejects `..` in paths |
| Blocked directory enforcement | ✅ Built | `.ssh`, Chrome User Data, etc. |
| Allowed directory allowlist | ✅ Built | Only configured roots accessible |
| Write-access gating | ✅ Built | Write operations require `HIGH_RISK` approval |
| Computer-control toggle | ✅ Built | `computer_control_enabled` config flag |
| L2/L3 approval generation | ✅ Built | Creates `ApprovalRequest` for external/high-risk |
| L0/L1 auto-allow in ASSIST mode | ✅ Built | Read and local actions pass without prompt |

**Gap:** No scoped grants, no expiration, no action-envelope metadata beyond permission level.

### 1.2 EmergencyStop (`src/agent/emergency.py`)

| Feature | Status | Notes |
|---|---|---|
| Global singleton | ✅ `GLOBAL_EMERGENCY_STOP` | Process-wide |
| `engage()` / `clear()` / `check()` | ✅ Built | Thread-safe via `threading.Event` |
| Listener callbacks on engage | ✅ Built | Cancels in-flight provider calls |
| Keyboard hotkey `Ctrl+Shift+Esc` | ✅ Built | Via `pynput.GlobalHotKeys` in `tray.py` |
| HUD Stop button | ✅ Built | `_on_stop()` in `jarvis_window.py` |
| Voice "Stop" command | ✅ Built | Via `match_control_command("stop")` |
| Blocks tool execution | ✅ Built | `emergency.check()` called before every tool exec |
| Cancels planner HTTP call | ✅ Built | `provider.cancel_inflight()` on engage |

**Gap:** No audit event on engage. No queue invalidation. No stale-approval clearing.
Post-stop task does not require explicit resume (auto-blocks but state is informal).

### 1.3 AuditLog (`src/agent/audit.py`)

| Feature | Status | Notes |
|---|---|---|
| In-memory ring buffer | ✅ Built | Max 5000 entries |
| Per-tool-execution recording | ✅ Built | `execute`, `denied`, `crash`, `awaiting_approval` |
| Secret redaction in results | ✅ Built | `redact_secrets()` catches `sk-`, `bearer`, key names |
| Thread-safe | ✅ Built | `threading.Lock` |
| Target/result truncation | ✅ Built | 200/2000 char caps |
| Query by task | ✅ Built | `for_task(task_id)` |
| Query recent | ✅ Built | `list_recent(limit)` |

**Gap:** No persistent storage (in-memory only, lost on restart). No event types beyond
tool execution. No user-facing activity view. No approval/denial events. No emergency-stop
events.

---

## 2. Existing Action Metadata

### 2.1 Tool Permission Levels (per tool)

Every tool declares `permission_level` on the class:

| Category | OBSERVE (L0) | LOCAL_ACTION (L1) | EXTERNAL_ACTION (L2) | HIGH_RISK (L3) |
|---|---|---|---|---|
| Screen tools | 3 | — | — | — |
| Computer tools | 3 | 14 | — | — |
| File tools | 7 | 5 | — | 2 |
| Browser tools | 18 | 17 | — | — |
| Core tools | 3 | 2 | — | — |
| **Total** | **34** | **38** | **0** | **2** |

**Gap:** No tool uses `EXTERNAL_ACTION` (L2). The two `HIGH_RISK` tools are `files.write`
and `files.overwrite`. Browser navigation, form submission, and communication actions are
all classified as `LOCAL_ACTION`, which auto-allows in ASSIST mode.

### 2.2 Phase 6 State-Changing Metadata (`tool_catalog.py`)

| Feature | Status | Notes |
|---|---|---|
| `is_state_changing(name)` | ✅ Built | Regex on tool names: click, type, delete, navigate, etc. |
| `ToolMeta.state_changing` | ✅ Built | Bool flag per tool |
| `ToolMeta.external_effect` | ✅ Built | Bool flag per tool |
| `ToolMeta.future_permission_level` | ✅ Built | Placeholder int, currently mirrors tool's declared level |

**Gap:** `external_effect` regex is broad (includes `navigate`, `open`) and doesn't
distinguish "open a local file" from "submit a form to a remote server." No `destructive`,
`reversible`, `financial`, `communication`, `authentication_related` flags.

---

## 3. Existing Tool Restrictions

### 3.1 File System (`src/agent/files/`)

| Restriction | Status |
|---|---|
| Allowed-root allowlist | ✅ `FilePathPolicy.allowed_roots` |
| Blocked-root blocklist | ✅ `FilePathPolicy.blocked_roots` |
| Read-only roots | ✅ `FilePathPolicy.readonly_roots` |
| Secret-path patterns | ✅ `is_secret_path()` |
| Symlink/junction escape prevention | ✅ Resolves then rechecks root |
| Directory traversal | ✅ Rejects `..` |
| Write-to-readonly rejection | ✅ `PathAccessError` |
| Max read size | ✅ `max_read_bytes` |

**Gap:** No `delete_to_recycle_bin` vs `permanent_delete` distinction. No bulk-operation
enumeration. No file-operation classification (READ/CREATE/MODIFY/DELETE).

### 3.2 Browser (`src/agent/browser/`)

| Restriction | Status |
|---|---|
| URL scheme allowlist | ✅ `http`, `https` only |
| Dangerous scheme blocklist | ✅ `javascript:`, `data:`, `file:`, `chrome:`, etc. |
| Host allowlist | ✅ Configurable |
| Host blocklist | ✅ Configurable |
| Typosquat protection | ✅ `canonical_site()` snaps near-misses |
| Navigation timeout | ✅ Configurable |

**Gap:** No form-submission classification. No download-execution prevention. No
distinction between "navigate to read" and "click Submit on a purchase page."

### 3.3 Computer Control (`src/agent/computer/`)

| Restriction | Status |
|---|---|
| `computer_control_enabled` global toggle | ✅ |
| Safe application resolution (no `shell=True`) | ✅ `shutil.which` + registry lookup |
| Command-injection prevention | ✅ `shell=False` in `Popen` |

**Gap:** No classification of what a keyboard/mouse action *does* semantically.
`computer.type_text` into a browser password field and `computer.type_text` into Notepad
are treated identically.

---

## 4. Existing User-Confirmation Mechanisms

| Mechanism | Status | Notes |
|---|---|---|
| `ApprovalRequest` model | ✅ Built | `id`, `task_id`, `action`, `purpose`, `target`, `details`, `status` |
| `PermissionDecision.requires_approval` | ✅ Built | Returned by `PermissionEngine.evaluate` |
| `ToolRegistry.pending_approvals` | ✅ Built | Dict of pending approval requests |
| `orchestrator.approve(id, bool)` | ✅ Built | Resolves a pending approval |
| `_after_approval_execute()` | ✅ Built | Force-executes after user approval |
| Voice "yes"/"no" | ⚠️ Partial | Handled by control commands but not bound to specific approvals |

**Gap:** No approval expiration. No approval-to-action binding validation. No stale-yes
protection. No approval UI in HUD. No scoped grants. No preview-before-commit.
A "yes" voice command has no mechanism to verify it matches a pending approval.

---

## 5. Existing Emergency Stop

See §1.2 above. Summary:

- ✅ Global singleton, thread-safe
- ✅ Keyboard, HUD button, and voice triggers
- ✅ Cancels in-flight HTTP and tool execution
- ⚠️ No audit event on engage
- ⚠️ No queue invalidation
- ⚠️ No stale-approval clearing
- ⚠️ Post-stop resume is informal (no revalidation)

---

## 6. Existing Logging

| Layer | What's logged | Persistent? |
|---|---|---|
| `AuditLog` | Tool executions, denials, crashes | ❌ In-memory only |
| `print()` statements | Routing, planner, Phase 6 steps | ✅ To `logs/jarvis.log` |
| `PerfTrace` | Timing spans per command | ❌ In-memory, returned to HUD |

**Gap:** No structured persistent audit. No user-facing activity history. No approval
events logged. No emergency-stop events logged.

---

## 7. Existing Secret Handling

| Feature | Status | Location |
|---|---|---|
| `redact_secrets()` | ✅ | `audit.py` — recursive dict/list/string redaction |
| `SENSITIVE_KEYS` set | ✅ | `audit.py` — `password`, `api_key`, `token`, `secret`, etc. |
| `SECRET_PATH_PATTERNS` | ✅ | `permissions/__init__.py` — 20+ filename patterns |
| `is_secret_path()` | ✅ | `permissions/__init__.py` and `files/permissions.py` |
| Bearer/sk- string detection | ✅ | `redact_secrets()` catches in free text |

**Gap:** No TTS secret filtering. No screenshot redaction for password fields. No
credential-provider integration. Secrets in planner prompts are not systematically blocked.

---

## 8. Existing Browser Safety Rules

See §3.2 above. The `UrlPolicy` is enforced at the `BrowserSession` level before any
navigation occurs. Typosquat protection (`canonical_site`) operates during plan validation.

---

## 9. Existing File Safety Rules

See §3.1 above. `FilePathPolicy` is enforced at the `FileSystemService` level before any
file operation. Secret paths are blocked at both the policy and permission-engine levels.

---

## 10. Existing External-Action Behavior

| External action | Current handling |
|---|---|
| Browser navigation | ✅ `LOCAL_ACTION` — auto-allowed |
| Browser form fill | ✅ `LOCAL_ACTION` — auto-allowed |
| Browser form submit | ⚠️ `LOCAL_ACTION` — auto-allowed, no distinction from fill |
| File create/write | ✅ `HIGH_RISK` — requires approval |
| File delete | ⚠️ Not implemented as a tool |
| Email send | ❌ No email integration yet |
| Purchase | ❌ No purchase integration yet |
| Application launch | ✅ `LOCAL_ACTION` — safe resolution |

---

## 11. Missing Capabilities

1. **ActionEnvelope** — No normalized action metadata before execution
2. **Risk classification** — Only 4 levels, no semantic classification (destructive, financial, etc.)
3. **PermissionPolicyEngine** — Current engine returns bool+approval, not rich decisions
4. **PermissionContext** — No scoped grants, no session/task-level authorization tracking
5. **Approval lifecycle** — No expiration, no binding validation, no invalidation on change
6. **Approval HUD** — No visual approval component
7. **Voice approval binding** — "Yes" not bound to specific pending approval
8. **Stale-yes protection** — No mechanism to prevent random "yes" from approving
9. **SecretRedactor** — Exists for audit but not for TTS or planner prompts
10. **Persistent audit** — In-memory only
11. **Activity history** — No user-facing timeline
12. **File-operation classification** — No READ/CREATE/MODIFY/DELETE distinction
13. **File-delete safety** — No recycle-bin vs permanent distinction
14. **Form-submit detection** — No browser submit classification
15. **Duplicate-action protection** — No idempotency tracking
16. **Rate/loop protection** — No repeated-action detection
17. **Dry-run mode** — No preview-without-execute
18. **TOCTOU protection** — No pre-execution revalidation
19. **Download-execution prevention** — No post-download guard
20. **Emergency-stop audit event** — Not recorded

---

## 12. Architecture to Reuse

| Component | Reuse for Phase 7 |
|---|---|
| `PermissionEngine` | Extend into `PermissionPolicyEngine` with richer decisions |
| `PermissionLevel` enum | Extend to 5 levels (add L4 HIGH_IMPACT) |
| `ApprovalRequest` model | Extend with expiration, scope, consequence fields |
| `EmergencyStop` | Extend with audit event and queue invalidation |
| `AuditLog` + `redact_secrets()` | Extend with event types and persistent storage |
| `ToolRegistry.execute()` | Insert `ActionEnvelope` creation before permission check |
| `BaseTool.permission_level` | Map to risk model levels |
| `ToolMeta.state_changing` / `external_effect` | Feed into `ActionEnvelope` flags |
| `FilePathPolicy` | Add file-operation classification |
| `UrlPolicy` | Unchanged — already sound |
| Phase 6 `WAITING_FOR_APPROVAL` status | Already in `TaskStatus` enum |
| Voice control commands | Extend for approval yes/no binding |

---

## 13. Components to Refactor

| Component | Refactoring needed |
|---|---|
| `PermissionEngine.evaluate()` | Return `PolicyDecision` (richer than `PermissionDecision`) |
| `ToolRegistry.execute()` | Create `ActionEnvelope` → pass to policy → then execute |
| `orchestrator.approve()` | Bind to specific action, validate not stale, check expiry |
| `match_control_command()` | Extend "yes"/"no" to check for pending approval context |
| `_handle_control()` | Route approval yes/no through policy, not legacy task system |
| File tools | Add operation-type metadata (READ/CREATE/MODIFY/DELETE) |
| Browser `click` tool | Detect if clicking a submit/purchase/send button |

---

## 14. Components to Create

| Component | Purpose |
|---|---|
| `ActionEnvelope` | Normalized metadata for every action before execution |
| `ActionRiskLevel` | 5-level enum: OBSERVE, LOCAL_REVERSIBLE, LOCAL_PERSISTENT, DESTRUCTIVE_EXTERNAL, HIGH_IMPACT |
| `PermissionPolicyEngine` | Rich policy evaluation replacing current `PermissionEngine.evaluate` |
| `PermissionContext` | Session/task-scoped grants with expiration |
| `ScopedGrant` | Bounded authorization for specific actions/targets/durations |
| `ApprovalRequest` (extended) | Add expiration, consequence, reversible, scope, action binding |
| `ApprovalHUD` | Visual approval component in JARVIS window |
| `SecretRedactor` (extended) | Apply to TTS output, planner prompts, screenshots |
| `AuditLog` (extended) | Event types, persistent storage, user-facing activity view |
| `DuplicateActionGuard` | Idempotency tracking for external actions |
| `RateProtection` | Repeated-action detection and pause |
| `DryRunMode` | Preview-without-execute for state-changing tools |
| `permissions.yaml` | Centralized policy configuration |
| `tests/test_phase7_permissions.py` | Comprehensive test suite |

---

## Summary

JARVIS already has a **solid security foundation** — the permission engine is enforced at
the tool boundary (not in the LLM), secret paths are blocked, file access is allowlisted,
browser schemes are restricted, and emergency stop works through keyboard/HUD/voice.

Phase 7 builds on this by adding: (1) richer action classification, (2) scoped and
expiring grants, (3) approval lifecycle with UI, (4) voice-approval binding, (5)
stale-yes/TOCTOU protection, (6) persistent audit with activity history, (7) secret
redaction in TTS/planner, (8) duplicate/rate protection, and (9) dry-run mode.

The architecture is designed so that **low-risk actions remain fast and uninterrupted**
while **consequential actions require explicit, bound, non-stale approval**.
