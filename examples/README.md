# Runnable Examples & Token-Saving Workflow (`or-free`)

- **Prerequisites:** Python 3.9+ (standard library only; no `pip install` required).
- **Safety first:** **Never put secrets, API keys, or proprietary source code in prompts.** The local partial credential filter (`Exit code: 3`) catches common token patterns only, public endpoints may log prompts, and automatic failover may try the next allowed provider unless `--no-fallback` or `-p <provider>` is set.
- **Always use `SKILL_DIR`:** Agents (including Grok Bot, Cursor, and Claude Code) run shell commands from `/workspace` or your project directory, **not** inside the skill folder. Set `SKILL_DIR` to the `Skill directory:` path reported when `SKILL.md` is loaded (for example `/home/box/agent-data/workflows/free-llm-router` or `/home/box/agent-data/workflows/free-llm-router-2`), and quote all paths.

---

## 1. Local status check (`--status`)

**Linux / macOS (`bash` / `zsh` — single-line):**

```bash
SKILL_DIR="/home/box/agent-data/workflows/free-llm-router" && python3 "$SKILL_DIR/scripts/or_free.py" --status
```

**Windows PowerShell (single-line, quoted path):**

```powershell
$SKILL_DIR = "C:\Users\Alice Smith\skills\free-llm-router"; py -3 "$SKILL_DIR\scripts\or_free.py" --status
```

**Windows CMD (`cmd.exe`):**

```bat
set "SKILL_DIR=C:\Users\Alice Smith\skills\free-llm-router" && py -3 "%SKILL_DIR%\scripts\or_free.py" --status
```

**Expected output (excerpt) & exit code:**

```text
=== Free LLM Router (or-free) Status ===
Registry file     : /home/box/.config/or-free/or_free_models_registry.json
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
SKILL_DIR="/home/box/agent-data/workflows/free-llm-router" && python3 "$SKILL_DIR/scripts/scan_and_sync_free_models.py" --apply --json > /tmp/or_free_scan.json
```

Progress logs go to `stderr`; machine-readable scan results go to `stdout` (`Exit code: 0`).

---

## 3. End-to-End Grok Bot Token-Saving Workflow Example

To genuinely save primary-agent tokens, the primary agent must delegate a **self-contained, non-sensitive subtask** with **minimal input**, cap output length with **`--max-tokens`**, and **verify/use the output without regenerating it**.

### Step A — User intent
> **User:** "Save primary-agent tokens: offload writing a standalone Python function `parse_iso_duration(s: str) -> int` (supporting `PT#H#M#S` with doctests) to a free model, then give me the verified code."

### Step B — Agent resolves `SKILL_DIR` from `/workspace` and delegates with bounded `--max-tokens`

The agent loads `Skill(name="free-llm-router")`, reads `Skill directory:` (e.g. `/home/box/agent-data/workflows/free-llm-router`), and runs a single-line command from `/workspace`:

```bash
SKILL_DIR="/home/box/agent-data/workflows/free-llm-router" && python3 "$SKILL_DIR/scripts/or_free.py" --caller "GrokBot" -t code --max-tokens 450 --json "Return ONLY a Python function parse_iso_duration(s: str) -> int for ISO-8601 PT#H#M#S strings with 2 concise doctests. No markdown prose."
```

### Step C — Expected stdout (`--json`) and `Exit code: 0`

```json
{
  "http_status": 200,
  "elapsed_ms": 1840.2,
  "caller": "GrokBot",
  "task": "code",
  "provider": "kilo",
  "keyless": true,
  "actual_model": "poolside/laguna-s-2.1:free",
  "billing_policy": "zero_price_verified",
  "usage": {
    "prompt_tokens": 46,
    "completion_tokens": 118,
    "total_tokens": 164,
    "cost_usd": 0
  },
  "content": "import re\n\ndef parse_iso_duration(s: str) -> int:\n    \"\"\"Parse PT#H#M#S into total seconds.\n    >>> parse_iso_duration('PT1H30M')\n    5400\n    >>> parse_iso_duration('PT45S')\n    45\n    \"\"\"\n    m = re.fullmatch(r'PT(?:(\\d+)H)?(?:(\\d+)M)?(?:(\\d+)S)?', s)\n    if not m or not any(m.groups()):\n        raise ValueError(f'Invalid duration: {s!r}')\n    h, mins, sec = (int(x or 0) for x in m.groups())\n    return h * 3600 + mins * 60 + sec"
}
```
`Exit code: 0`

### Step D — Primary agent verification, stop condition & honest token reporting

1. **Verify & stop:** The primary agent sees `Exit code: 0` and `"http_status": 200`, inspects `"content"`, optionally runs `python3 -m doctest` on the snippet, and presents the function directly to the user **without rewriting or regenerating the solution from scratch**.
2. **Honest measurement:** The `usage.total_tokens` (`164`) represents **offloaded free-endpoint tokens**, **not** net primary-agent tokens saved (because the primary agent still spent tokens invoking the shell tool and reading the compact JSON result). Never report unmeasured percentage savings.

---


---

## 3b. Summarization (public text only)

```bash
SKILL_DIR="/home/box/agent-data/workflows/free-llm-router" && python3 "$SKILL_DIR/scripts/or_free.py" --caller "GrokBot" -t general --max-tokens 300 "Summarize this PUBLIC changelog in 5 bullets (no secrets): v1.2 adds retry backoff; v1.3 drops Python 3.8."
```

Primary agent: check `Exit code: 0`, paste the 5 bullets, do **not** rewrite the summary from scratch.

## 3c. Brainstorming (non-sensitive naming options)

```bash
SKILL_DIR="/home/box/agent-data/workflows/free-llm-router" && python3 "$SKILL_DIR/scripts/or_free.py" --caller "GrokBot" -t fast --max-tokens 200 "Suggest 8 concise English names for a CLI that routes free LLM calls. One name per line, no explanations."
```

Primary agent: pick 1–2 names from the list after a quick sanity check; do not regenerate a competing list.

## 4. Accurate Exit Code Reference

| Exit Code | Meaning |
|---:|---|
| `0` | **Success:** route candidate returned a response (or `--status` / `--validate-registry` succeeded). |
| `1` | **All candidates failed / runtime or lock error:** all allowed route candidates failed/exhausted, general runtime error, or state lock/persistence failure. |
| `2` | **Blocked by zero-cost policy gate:** requested model/provider failed `is_safe_free_target` (paid model, missing `$0/$0` catalog proof, or unverified provider without `--allow-unverified-model`). |
| `3` | **Blocked by local DLP filter:** prompt matched the partial credential filter before any network request. |
| `4` | **Strict registry or TTL error:** strict registry file (`--registry` or `OR_FREE_REGISTRY_PATH`) is missing, invalid, or older than `--max-registry-age-hours`, or `--max-registry-age-hours` is invalid. |
