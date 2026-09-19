# Phase 7 — Performance Report

**Date:** September 17, 2026
**Method:** 1,000-iteration average per measurement, in-process Python

---

## Policy Evaluation Latency

| Tool                  | Risk Level             | Latency (ms) |
|-----------------------|------------------------|--------------|
| `browser.read_page`   | OBSERVE                | 0.0008       |
| `browser.navigate`    | LOCAL_REVERSIBLE       | 0.0008       |
| `files.read`          | OBSERVE                | 0.0008       |
| `files.write`         | LOCAL_PERSISTENT       | 0.0011       |
| `files.delete`        | DESTRUCTIVE_EXTERNAL   | 0.0011       |
| `email.send`          | DESTRUCTIVE_EXTERNAL   | 0.0011       |
| `purchase.checkout`   | HIGH_IMPACT            | 0.0010       |

**All policy evaluations < 0.002 ms** — effectively zero overhead for the user.

---

## Approval Latency

| Operation          | Latency (ms) |
|--------------------|--------------|
| Create approval    | 0.0047       |
| Resolve (voice)    | 0.0001       |

---

## Audit Log Latency

| Operation          | Latency (ms) |
|--------------------|--------------|
| Write (in-memory)  | 0.0022       |

---

## Secret Redaction Latency

| Operation          | Latency (ms) |
|--------------------|--------------|
| Redact text         | 0.0051       |

---

## Full ExecutionGate Latency

| Action             | Latency (ms) | Components                          |
|--------------------|--------------|-------------------------------------|
| Navigate (L1)      | 0.0183       | Envelope + duplicate + rate + policy + audit |
| Read page (L0)     | 0.0127       | Envelope + duplicate + rate + policy + audit |

---

## Impact Assessment

### Phase 5 Fast Paths
- `browser.read_page` gate overhead: **0.013 ms** — negligible vs. 50-200ms network call
- No approval prompt for read operations — zero user interruption

### Phase 6 Task Execution
- Multi-step low-risk workflows (open, navigate, fill): all auto-ALLOW at L0/L1
- Policy adds < 0.02ms per tool call — invisible within 100-2000ms tool execution times
- High-risk steps (delete, send, submit) pause for approval, which is user-expected

### Emergency Stop
- Kill switch uses `threading.Event.is_set()` — effectively instant (< 0.001 ms)
- Emergency check is the first thing the gate evaluates

---

## Summary

Phase 7 adds **< 0.02 ms** of overhead per tool call. This is **under 0.1%** of
the typical tool execution time.  Low-risk actions remain as fast as before.
The permission system adds security without adding latency.
