# Runnable Examples & Token-Saving Workflow (`or-free`)

- **Prerequisites:** Python 3.9+ (standard library only; no `pip install` required).
- **Safety first:** **Never put secrets, API keys, or proprietary source code in prompts.** Free models only receive prompts with an allowlisted `--task-class` (`public_research`, `public_copy`, `brainstorm`, `general_knowledge`, `sanitized_data`, `public_summary`, `sanitized_log_preprocessing`, `public_text_classification`, `public_document_extraction`). Coding tasks (`-t code` / `--task-class code`) and untagged prompts return `BYPASS_TO_PRIMARY` (exit 0, no model reply) — do them on the primary model. The local partial credential filter (`Exit code: 3`) catches common token patterns only, a local code/config/infra scan runs on every prompt, public endpoints may log prompts, and automatic failover may try the next allowed provider unless `--no-fallback` or `-p <provider>` is set.
- **Always use `SKILL_DIR`:** Agents (including Grok Bot, Cursor, and Claude Code) run shell commands from `/workspace` or your project directory, **not** inside the skill folder. Set `SKILL_DIR` to the `Skill directory:` path reported when `SKILL.md` is loaded (for example `$HOME/agent-data/workflows/free-llm-router` or `$HOME/agent-data/workflows/free-llm-router-2`), and quote all paths.

---

## 1. Local status check (`--status`)

**Linux / macOS (`bash` / `zsh` — single-line):**

```bash
SKILL_DIR="$HOME/agent-data/workflows/free-llm-router" && python3 "$SKILL_DIR/scripts/or_free.py" --status
```

**Windows PowerShell (single-line, quoted path):**

```powershell
$SKILL_DIR = "$env:USERPROFILE\skills\free-llm-router"; py -3 "$SKILL_DIR\scripts\or_free.py" --status
```

**Windows CMD (`cmd.exe`):**

```bat
set "SKILL_DIR=%USERPROFILE%\skills\free-llm-router" && py -3 "%SKILL_DIR%\scripts\or_free.py" --status
```

**Expected output (excerpt) & exit code:**

```text
=== Free LLM Router (or-free) Status ===
Registry file     : ~/.config/or-free/or_free_models_registry.json
Pricing verified  : 12 models ($0/$0 catalog proof)
Keyless allowlist : 3 models (OVH / LLM7 curated)
Providers         : kilo=READY, llm7=READY, ovh=READY, openrouter=SKIP (waiting_for_key)
Exit code: 0
```

*Note:* `--status` returning `Exit code: 0` confirms that local configuration and the registry snapshot loaded cleanly — it does **not** test live upstream network reachability.

---

## 2. Refresh the free-model registry (`--apply`)

Run a live catalog scan and `"42"` health probe so `$OR_FREE_HOME/or_free_models_registry.json` has fresh `$0/$0` catalog proofs (never modifies `$SKILL_DIR`):

```bash
SKILL_DIR="$HOME/agent-data/workflows/free-llm-router" && python3 "$SKILL_DIR/scripts/scan_and_sync_free_models.py" --apply --json > /tmp/or_free_scan.json
```

Progress logs go to `stderr`; machine-readable scan results go to `stdout` (`Exit code: 0`).

---

## 3. End-to-End Grok Bot Token-Saving Workflow Example

To genuinely save primary-agent tokens, the primary agent must delegate a **self-contained public subtask** that carries an **allowlisted `--task-class`**, with **minimal input**, cap output length with **`--max-tokens`**, and **verify/use the output without regenerating it**.

### Step A — User intent
> **User:** "Save primary-agent tokens: offload a 5-bullet summary of this public changelog to a free model, then give me the bullets."

### Step B — Agent resolves `SKILL_DIR` from `/workspace` and delegates with an allowlisted class

The agent loads `Skill(name="free-llm-router")`, reads `Skill directory:` (e.g. `$HOME/agent-data/workflows/free-llm-router`), and runs a single-line command from `/workspace`:

```bash
SKILL_DIR="$HOME/agent-data/workflows/free-llm-router" && python3 "$SKILL_DIR/scripts/or_free.py" --caller "GrokBot" -t general --task-class public_summary --max-tokens 300 --json "Summarize this public changelog in 5 bullets: version 1.2 adds retry backoff; version 1.3 drops Python 3.8."
```

### Step C — Expected stdout (`--json`) and `Exit code: 0`

Illustrative shape after a keyless model accepts the allowlisted prompt (provider, model id, and token counts vary):

```json
{
  "http_status": 200,
  "elapsed_ms": 1840.2,
  "caller": "GrokBot",
  "task": "general",
  "provider": "kilo",
  "keyless": true,
  "actual_model": "poolside/laguna-s-2.1:free",
  "billing_policy": "zero_price_verified",
  "usage": {
    "prompt_tokens": 42,
    "completion_tokens": 80,
    "total_tokens": 122,
    "cost_usd": null
  },
  "content": "- Version 1.2 adds retry backoff.\n- Version 1.3 drops Python 3.8."
}
```
`Exit code: 0`

### Step D — Primary agent verification, stop condition & honest token reporting

1. **Verify & stop:** The primary agent sees `Exit code: 0` and `"http_status": 200`, inspects `"content"`, and presents the bullets directly to the user **without rewriting or regenerating the summary**. If `decision` is `BYPASS_TO_PRIMARY`, there is no model reply — do the task on the primary model.
2. **Honest measurement:** `usage.total_tokens` represents **offloaded free-endpoint tokens**, **not** net primary-agent tokens saved (because the primary agent still spent tokens invoking the shell tool and reading the compact JSON result). Never report unmeasured percentage savings. When the upstream response omits cost, `cost_usd` is `null`.

---

## 3b. Summarization (public text only)

```bash
SKILL_DIR="$HOME/agent-data/workflows/free-llm-router" && python3 "$SKILL_DIR/scripts/or_free.py" --caller "GrokBot" -t general --task-class public_summary --max-tokens 300 "Summarize this public changelog in 5 bullets: version 1.2 adds retry backoff; version 1.3 drops Python 3.8."
```

Primary agent: check `Exit code: 0` and that stdout is the summary (not a `BYPASS_TO_PRIMARY` decision), paste the bullets, and do **not** rewrite the summary from scratch.

## 3c. Brainstorming (public naming options)

```bash
SKILL_DIR="$HOME/agent-data/workflows/free-llm-router" && python3 "$SKILL_DIR/scripts/or_free.py" --caller "GrokBot" -t fast --task-class brainstorm --max-tokens 200 "Suggest 8 concise English names for a cozy cat puzzle game. One name per line, no explanations."
```

Primary agent: pick 1–2 names from the list after a quick sanity check; do not regenerate a competing list.

## 3d. Quota policy gate (`--check-policy`)

Check the default-deny content gate and the Grok Ultra split before offloading. The content gate applies even with no `quota_policy.json`: an allowlisted class whose prompt passes the scanner yields `POLICY_UNAVAILABLE` (free pool allowed); coding and untagged prompts yield `BYPASS_TO_PRIMARY`. `--check-policy` always exits `0`.

```bash
SKILL_DIR="$HOME/agent-data/workflows/free-llm-router" && python3 "$SKILL_DIR/scripts/or_free.py" --check-policy --json --task-class public_copy "Write a tweet announcing a cozy cat puzzle game"
```

Read `decision`: `USE_FREE` means offload; `BYPASS_TO_PRIMARY` means keep the task on the primary model; `POLICY_UNAVAILABLE` means the content gate passed and there is no readable policy file, so offload to the free pool.

**`BYPASS_TO_PRIMARY` exits `0` with no model reply** (one decision JSON line, no provider request). Check `decision` before treating stdout as an answer.

Place a policy file at `$OR_FREE_HOME/quota_policy.json` (or set `OR_FREE_QUOTA_POLICY_PATH`). An expired cycle rolls forward to tier `blue` with `usage.status` `unknown`. When the content gate has already passed, unknown usage routes as conservative blue (`USE_FREE`). A policy `routing.free_allowlist` can only narrow the hardcoded allowlist; it cannot add `code` or `verified_low_risk_code_candidate`. Minimal placeholder:

```json
{
  "revision": 1,
  "cycle": {
    "cycle_id": "example-cycle",
    "starts_at": "2026-01-01T00:00:00Z",
    "reset_at": "2026-01-08T00:00:00Z"
  },
  "usage": {
    "status": "unknown",
    "used_fraction": null,
    "observed_at": null,
    "max_age_seconds": 600
  },
  "runtime": {
    "tier": "blue"
  }
}
```

## 3e. Coding and untagged prompts (`BYPASS_TO_PRIMARY`)

These do **not** reach a free model. The router prints one decision JSON line, sends no request, and exits `0` with no model reply. Do the work on the primary model.

```bash
# --task-class code (and -t code with no allowlisted class) => BYPASS_TO_PRIMARY
SKILL_DIR="$HOME/agent-data/workflows/free-llm-router" && python3 "$SKILL_DIR/scripts/or_free.py" --caller "GrokBot" -t code --task-class code --max-tokens 450 --json "Return ONLY a Python function parse_iso_duration(s: str) -> int for ISO-8601 duration strings with 2 concise doctests."

# Untagged prompt (no --task-class) => BYPASS_TO_PRIMARY
SKILL_DIR="$HOME/agent-data/workflows/free-llm-router" && python3 "$SKILL_DIR/scripts/or_free.py" -t fast --max-tokens 64 "Say hello in 5 words"
```

## 4. Accurate Exit Code Reference

| Exit Code | Meaning |
|---:|---|
| `0` | **Success:** route candidate returned a response, or `--status` / `--validate-registry` / `--check-policy` succeeded. A content-gate or quota-gated `BYPASS_TO_PRIMARY` also exits `0` with decision JSON only (no model reply) — check `decision`. |
| `1` | **All candidates failed / runtime or lock error:** all allowed route candidates failed/exhausted, general runtime error, or state lock/persistence failure. |
| `2` | **Blocked by zero-cost policy gate:** requested model/provider failed `is_safe_free_target` (paid model, missing `$0/$0` catalog proof, or unverified provider without `--allow-unverified-model`). |
| `3` | **Blocked by local DLP filter:** prompt matched the partial credential filter before any network request. |
| `4` | **Strict registry or TTL error:** strict registry file (`--registry` or `OR_FREE_REGISTRY_PATH`) is missing, invalid, or older than `--max-registry-age-hours`, or `--max-registry-age-hours` is invalid. |
