# Free LLM Router (`or-free`): OpenRouter and Multi-Provider Catalog Scanner

Save primary-agent tokens and reduce LLM API costs by routing **allowlisted public tasks** to free LLMs (OpenRouter `:free`, Kilo, OVH, LLM7) from **Grok Bot, Cursor, Claude Code, Codex**, or any Python agent — stdlib-only, with a multi-provider free-model catalog scanner. **Default-deny:** only prompts tagged with an allowlisted `--task-class` (`public_research`, `public_copy`, `brainstorm`, `general_knowledge`, `sanitized_data`, `public_summary`, `sanitized_log_preprocessing`, `public_text_classification`, `public_document_extraction`) may reach a free model. Coding tasks (`-t code` / `--task-class code`) and untagged prompts return `BYPASS_TO_PRIMARY` (exit 0, no model reply) and stay on the primary model.

- **Prerequisites:** Python 3.9+ (standard library only; zero `pip install` dependencies).
- **Identity & Invocation:** skill folder `free-llm-router` → CLI brand `or-free` → script `"$SKILL_DIR/scripts/or_free.py"`. Copying this folder does **not** install a global `or-free` binary on `PATH`; always invoke `python3 "$SKILL_DIR/scripts/or_free.py"` (or define an optional shell alias).
- **Default State Directories (no need to set `OR_FREE_HOME` first):**
  - **Linux / macOS:** `~/.config/or-free` (or `$XDG_CONFIG_HOME/or-free` when set)
  - **Windows:** `%LOCALAPPDATA%\or-free`
  - **Optional override:** set `OR_FREE_HOME="/custom/state/dir"`

> **Honesty boundary:** free routes are checked against saved catalog snapshots (`$0/$0` prompt and completion pricing for OpenRouter/Kilo) and curated keyless allowlists (OVH/LLM7), but providers can change pricing or rate limits at any time — **zero billing is not guaranteed**, and free-model capability fluctuates with public pool availability. Public endpoints may log prompts; never send secrets.

---

## Quick Start (3 Steps for Grok Bot, Cursor & Claude Code)

### Step 1: Install the skill folder into your host agent

| Host Agent | Project Scope | User / Global Scope | Notes |
|---|---|---|---|
| **Grok Bot** | — | `~/agent-data/workflows/free-llm-router/` | Extract archive into `~/agent-data/workflows/free-llm-router/`, **or** ask Grok Bot to register/update the skill with `UpdateSkill`. If `UpdateSkill` assigns an ID like `free-llm-router-2`, loading the skill reveals the exact `Skill directory:` path. Change this path if the skill is installed elsewhere. |
| **Cursor** | `.cursor/skills/free-llm-router/` | `~/.cursor/skills/free-llm-router/` | Keep `SKILL.md` and `scripts/` together inside the folder. |
| **Claude Code / Codex** | `.claude/skills/free-llm-router/` | `~/.claude/skills/free-llm-router/` | Works with read-only skill mounts (`chmod -R a-w`). |

### Step 2: Confirm discovery & resolve `SKILL_DIR`

Because agents run shell commands from `/workspace` or your project root (not inside the skill folder), running a relative `python3 scripts/or_free.py` from `/workspace` will fail with `FileNotFoundError`.
- In **Grok Bot** (or Claude Code / Cursor), load `Skill(name="free-llm-router")` (or type `/free-llm-router` in chat) and read the `Skill directory:` path returned by the tool.
- Set `SKILL_DIR` to that absolute path and **always quote paths** so spaces on macOS/Windows never break commands.

### Step 3: Verify status, refresh live catalog, and run your first keyless prompt

**Linux / macOS (`bash` / `zsh` — single-line commands):**

```bash
SKILL_DIR="$HOME/agent-data/workflows/free-llm-router"
python3 "$SKILL_DIR/scripts/or_free.py" --status
python3 "$SKILL_DIR/scripts/scan_and_sync_free_models.py" --apply
python3 "$SKILL_DIR/scripts/or_free.py" -t fast --task-class general_knowledge --max-tokens 64 "Say hello in 5 words"
```

**Expected first-run behavior & cold-start troubleshooting:**
- `--status` prints the loaded registry path, snapshot `updated_at` timestamp, security gates, and provider readiness (`kilo` / `llm7` / `ovh` `READY` in keyless mode; `openrouter` `SKIP (waiting_for_key)` until an OpenRouter key is configured) and exits with **`Exit code: 0`**.
  - *What `SKIP (waiting_for_key)` means:* normal in 0-key mode; the router automatically skips OpenRouter and routes through keyless `kilo`, `ovh`, and `llm7`.
  - *Important:* `--status` succeeding (`Exit code: 0`) only confirms local config and registry snapshot loading — it does **not** test live upstream network reachability.
- `scan_and_sync_free_models.py --apply` fetches live provider catalogs, runs `"42"` health probes, and writes a fresh registry snapshot to `$OR_FREE_HOME/or_free_models_registry.json` (never modifying `$SKILL_DIR`). Running `--apply` right after install is recommended because the bundled seed (`scripts/or_free_models_registry.json`) ages over time.
- `-t fast --task-class general_knowledge --max-tokens 64 "Say hello in 5 words"` routes to a live keyless model, prints a short greeting (e.g. `Hello there, how are you?`), and exits with **`Exit code: 0`**.
  - *If `-t fast` returns `Exit code: 1` (all keyless candidates temporarily rate-limited or unreachable):* (1) run `scan_and_sync_free_models.py --apply` to refresh live pool availability, (2) retry after 30–60s, or (3) set a free-tier `OPENROUTER_API_KEY` to unlock additional `$0/$0` `:free` models.
  - *Coding and untagged prompts:* `-t code`, `--task-class code`, and any prompt without an allowlisted `--task-class` do **not** reach a free model. They print one `BYPASS_TO_PRIMARY` decision JSON line and exit **`0` with no model reply**. Do that work on the primary model.

**Windows PowerShell (single-line commands, quoted for paths with spaces):**

```powershell
py -3 "$env:USERPROFILE\skills\free-llm-router\scripts\or_free.py" --status
py -3 "$env:USERPROFILE\skills\free-llm-router\scripts\scan_and_sync_free_models.py" --apply
py -3 "$env:USERPROFILE\skills\free-llm-router\scripts\or_free.py" -t fast --task-class general_knowledge --max-tokens 64 "Say hello in 5 words"
```

**Windows CMD (`cmd.exe`):**

```bat
set "SKILL_DIR=%USERPROFILE%\skills\free-llm-router"
py -3 "%SKILL_DIR%\scripts\or_free.py" --status
py -3 "%SKILL_DIR%\scripts\scan_and_sync_free_models.py" --apply
py -3 "%SKILL_DIR%\scripts\or_free.py" -t fast --task-class general_knowledge --max-tokens 64 "Say hello in 5 words"
```

Examples: [examples/README.md](examples/README.md) · Policy details: [references/provider-and-registry-policy.md](references/provider-and-registry-policy.md)

---

## What tasks should I offload? (Quick FAQ)

| Suitable (allowlisted `--task-class`) | Avoid (`BYPASS_TO_PRIMARY` or never send) |
|---|---|
| Public research and public copy (`public_research`, `public_copy`) | Coding tasks (`-t code`, `--task-class code`, source, configs) — primary model |
| Public changelog / docs summarization (`public_summary`) | Untagged prompts (no `--task-class`) — primary model |
| General-knowledge explanations with no project code (`general_knowledge`) | Secrets, API keys, customer PII, proprietary source |
| Brainstorming names, outlines, idea lists (`brainstorm`) | Infra, internal hosts, architecture, production config |
| Already de-identified data (`sanitized_data` and the legacy sanitized/public classes) | Anything you cannot send to a public free endpoint |

Rule of thumb: offload only when the prompt is plain public prose and you pass an allowlisted `--task-class`. Coding and untagged prompts return `BYPASS_TO_PRIMARY`; do them on the primary model. The primary agent still verifies a short free-model answer without regenerating it.

## How do I actually save primary-agent tokens with `or-free`?

Simply running a free model does not automatically save primary-agent tokens if the agent sends huge prompts, reads back multi-page outputs, or regenerates the answer itself. Follow this delegation contract:

1. **Delegate only allowlisted public task classes:** `public_research`, `public_copy`, `brainstorm`, `general_knowledge`, `sanitized_data`, `public_summary`, `sanitized_log_preprocessing`, `public_text_classification`, `public_document_extraction`.
2. **Keep coding and untagged work on the primary model:** `-t code`, `--task-class code`, a missing `--task-class`, and any prompt the local code/config/infra scanner flags return `BYPASS_TO_PRIMARY` (exit `0`, decision JSON only, no model reply).
3. **Send minimal input & cap output (`--max-tokens`):** pass only the required public context and set `--max-tokens 400`–`800` so the tool output returned into the primary agent's context window stays compact.
4. **Verify the decision, then stop:** `Exit code: 0` with `BYPASS_TO_PRIMARY` means there is no free-model answer. `Exit code: 0` with a model reply (`http_status` 200 in `--json`) means the primary agent sanity-checks that text and uses it **without regenerating the same answer**.
5. **Measure honestly:** the `usage` object in `--json` output reports **offloaded free-endpoint tokens**, not net primary-agent tokens saved (since the primary agent still spends tokens invoking the tool and reading the result).

See [examples/README.md](examples/README.md) for a complete end-to-end **Grok Bot Token-Saving Workflow Example**.

---

## Quota policy gate

Before each call, `or_free.py` applies the **default-deny content gate**, then reads `quota_policy.json` (`OR_FREE_QUOTA_POLICY_PATH`, otherwise `$OR_FREE_HOME/quota_policy.json`). The content gate runs even when no policy file exists. Missing `--task-class`, `auto` / `general` / `code` / `long` / `fast`, any class outside the allowlist, a sensitivity other than `public` / `sanitized` / `deidentified`, and any prompt the local code/config/infra scanner flags all return `BYPASS_TO_PRIMARY`. A policy file's `routing.free_allowlist` can only **narrow** the hardcoded allowlist; it cannot add classes (including `code` or `verified_low_risk_code_candidate`). Only content that passes the gate and has no readable policy file gets `POLICY_UNAVAILABLE` (free pool allowed).

```bash
python3 "$SKILL_DIR/scripts/or_free.py" --check-policy --json --task-class public_copy "Write a tweet announcing a cozy cat puzzle game"
```

`--check-policy` always exits `0` and prints decision JSON. Pass the prompt (positional text or `--prompt-file`) so DLP and the code/config scanner run. Without a prompt, `content_scanned` is false; a real call always scans. Read `decision`:

| `decision` | What to do |
|---|---|
| `USE_FREE` | Offload to the free pool. |
| `BYPASS_TO_PRIMARY` | Keep the task on the primary model. This includes coding and untagged prompts. |
| `POLICY_UNAVAILABLE` | Content gate passed and there is no readable policy file. Offload to the free pool. |

**`BYPASS_TO_PRIMARY` exits `0` with no model reply.** A gated call prints one decision JSON line and does not contact a provider. Callers must check `decision`, not only the exit code, before treating stdout as a model answer.

An expired `cycle.reset_at` rolls forward automatically (same length, default 7 days): usage becomes `unknown`, tier becomes `blue`, and `revision` increments. When the content gate has already passed, unknown usage routes as conservative blue (`USE_FREE`). Set `OR_FREE_POLICY_NO_ROLLOVER=1` to renew in memory only. Write `usage.status` to `known` and set `used_fraction` (0–1) when a live sample is available.

Minimal `quota_policy.json` (placeholder values only):

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

---

## Saved registry snapshot vs. live catalog scans (and TTL)

- **Normal `or_free.py` calls do NOT fetch live catalogs on every prompt.** They read the saved registry snapshot in `$OR_FREE_HOME/or_free_models_registry.json` (falling back to the bundled seed `scripts/or_free_models_registry.json` on first run before `--apply` has been run).
- **Check snapshot age:** run `python3 "$SKILL_DIR/scripts/or_free.py" --status` to inspect the registry `updated_at` timestamp.
- **Refresh live catalog proofs:** run `python3 "$SKILL_DIR/scripts/scan_and_sync_free_models.py" --apply` (writes only to `$OR_FREE_HOME`).
- **Enforce a hard freshness TTL:** pass `--max-registry-age-hours 24` or set `OR_FREE_MAX_REGISTRY_AGE_HOURS=24`. When set, catalog price proofs older than the TTL are rejected (and strict registries exit with code `4`).

| Provider | How zero price is checked |
|---|---|
| **OpenRouter** & **Kilo** | Live catalog scan must show `pricing.prompt == 0` **and** `pricing.completion == 0`, recorded in registry `pricing_verified_models`. A bare `:free` suffix is not enough. Empty or expired proof never falls back to built-in seeds. |
| **OVH** & **LLM7** | **Curated keyless allowlist** only (`curated_keyless_allowlist_models`); no catalog `$0/$0` claim. Keyless public endpoints with no billing account attached. |
| **NVIDIA** / **RouteOpen** | Denied by default. Only enabled with `--allow-unverified-model` (prints stderr warning; cost not guaranteed). |

Other gates: HTTPS host whitelist (no redirects), model-ID regex and `<7B`/classifier filters, `task_pools` cannot self-certify models, live `"42"` probe before registry commit. Details: [references/provider-and-registry-policy.md](references/provider-and-registry-policy.md).

---

## Unified key resolution, environment variables, and exit codes

Router and scanner resolve keys in the same order:

1. Environment: `OPENROUTER_API_KEY`, then `OR_API_KEY`; `NVIDIA_API_KEY`; `ROUTEOPEN_API_KEY`.
2. Plain key files (first non-empty, non-comment line): `<dir>/openrouter_api_key`, then `<dir>/api_key` across `$OR_FREE_HOME`, `~/.config/openrouter`, and `~/.ob1` *(optional OB-1 local router integration; most users can ignore)*.
3. `keys.env` (`KEY=VALUE` or `export KEY=VALUE`) in the same directories.

| Variable | Purpose |
|---|---|
| `OPENROUTER_API_KEY` / `OR_API_KEY` | Optional free-tier OpenRouter key; unlocks `:free` models that also pass `$0/$0` catalog checks |
| `OR_FREE_HOME` | Optional override for config/state/log directory (defaults: `~/.config/or-free` on Linux/macOS, `%LOCALAPPDATA%\or-free` on Windows). Default policy path is `$OR_FREE_HOME/quota_policy.json` |
| `OR_FREE_QUOTA_POLICY_PATH` | Optional path to `quota_policy.json`. Missing file means no quota gating |
| `OR_FREE_POLICY_NO_ROLLOVER` | Set to `1` to renew an expired cycle in memory only (do not write the policy file back) |
| `OR_FREE_INTERNAL_DOMAINS` | Comma-separated company domains the content scanner always treats as private (for example `example.com,studio.io`) |
| `OR_FREE_REGISTRY_PATH` | **Strict** registry path (overridden by `--registry`; exits with code `4` on missing/invalid/stale file) |
| `OR_FREE_MAX_REGISTRY_AGE_HOURS` | Reject catalog price proofs older than `<hours>` (finite `> 0`) |
| `XDG_CONFIG_HOME` | Linux/macOS config root (`$XDG_CONFIG_HOME/or-free` when `OR_FREE_HOME` is unset) |
| `LOCALAPPDATA` | Windows config root (`%LOCALAPPDATA%\or-free` when `OR_FREE_HOME` is unset) |
| `OR_FREE_AUDIT_PREVIEW=1` | Opt-in 60-char prompt/reply previews in local audit logs |
| `NVIDIA_API_KEY` / `ROUTEOPEN_API_KEY` | Used only when `--allow-unverified-model` is explicitly passed |

### Exit Codes

| Code | Meaning |
|---:|---|
| `0` | **Success:** route candidate returned a valid response, or `--status` / `--validate-registry` / `--check-policy` succeeded. A content-gate or quota-gated `BYPASS_TO_PRIMARY` also exits `0` and prints decision JSON only (no model reply) — check `decision`. |
| `1` | **Candidates exhausted or runtime/lock error:** all allowed route candidates failed/timed out, general runtime error, or state lock/persistence failure. |
| `2` | **Blocked by zero-cost policy gate:** requested model/provider failed `is_safe_free_target` (paid model, missing `$0/$0` catalog proof, or unverified provider without `--allow-unverified-model`). |
| `3` | **Blocked by local DLP filter:** prompt matched the partial credential filter before any network request was sent. |
| `4` | **Strict registry or TTL error:** strict registry file (`--registry` or `OR_FREE_REGISTRY_PATH`) is missing, invalid, or older than `--max-registry-age-hours`, or `--max-registry-age-hours` value is invalid. |

Registry load order (never loaded from CWD): `--registry` (strict) → `OR_FREE_REGISTRY_PATH` (strict) → `$OR_FREE_HOME/or_free_models_registry.json` → bundled `scripts/or_free_models_registry.json`.

---

## How do I refresh free models and schedule daily scans?

### Standard user scan commands (safe for read-only skill installs)

```bash
SKILL_DIR="$HOME/agent-data/workflows/free-llm-router"
python3 "$SKILL_DIR/scripts/scan_and_sync_free_models.py" --apply
python3 "$SKILL_DIR/scripts/scan_and_sync_free_models.py" --apply --json
python3 "$SKILL_DIR/scripts/or_free.py" --validate-registry "$HOME/.config/or-free/or_free_models_registry.json"
```

`--apply` writes only to `$OR_FREE_HOME` by default (`$OR_FREE_HOME/or_free_models_registry.json` and `$OR_FREE_HOME/free_model_scan_latest.md`). Optional flags include `--output-dir <dir>` and `--sync-ob1` *(optional OB-1 local router integration that merges `or_free_task_pools` into `~/.ob1/config.json`; most users can ignore)*.

Registry writes are atomic and locked, with per-run backups and rollback on failure. See [references/provider-and-registry-policy.md](references/provider-and-registry-policy.md).

### Scheduled daily scan (always use a full path to the script)

- **Linux (`cron`):**
  ```cron
  15 4 * * * /usr/bin/python3 "$HOME/agent-data/workflows/free-llm-router/scripts/scan_and_sync_free_models.py" --apply >/dev/null 2>&1
  ```
- **macOS (`launchd`):** in `~/Library/LaunchAgents/com.or-free.scanner.plist`, set `ProgramArguments` to `["/usr/bin/python3", "$HOME/.claude/skills/free-llm-router/scripts/scan_and_sync_free_models.py", "--apply"]` with `StartCalendarInterval` (`Hour=4`, `Minute=15`), then load with `launchctl load ~/Library/LaunchAgents/com.or-free.scanner.plist`. Write the expanded home path into the plist, and change it if the skill is installed elsewhere.
- **Windows (`schtasks` — CMD / PowerShell):**
  CMD (`cmd.exe`):
  ```bat
  schtasks /Create /SC DAILY /TN "or-free-daily-scan" /TR "py -3 \"%USERPROFILE%\skills\free-llm-router\scripts\scan_and_sync_free_models.py\" --apply" /ST 04:15
  ```
  PowerShell (single quotes so the `\"` escapes are passed through to `schtasks`):
  ```powershell
  schtasks /Create /SC DAILY /TN "or-free-daily-scan" /TR 'py -3 \"%USERPROFILE%\skills\free-llm-router\scripts\scan_and_sync_free_models.py\" --apply' /ST 04:15
  ```

### Maintainer-only: updating the bundled seed in the repository

Normal users should **never** run `--update-bundled-seed`. It requires write permissions on `$SKILL_DIR` (it will fail with `PermissionError` on read-only skill mounts `chmod -R a-w`) and overwrites `scripts/or_free_models_registry.json` for release packaging. Normal users must use `--apply` instead:

```bash
# MAINTAINERS ONLY — do not run in normal user installations
python3 "$SKILL_DIR/scripts/scan_and_sync_free_models.py" --apply --update-bundled-seed
```

---

## What data is sent to providers?

- **Outbound prompt traffic:** your prompt (and optional system prompt) is sent over HTTPS to the selected provider. Public free endpoints may log or retain prompts — never send secrets or proprietary code.
- **Multi-provider failover behavior:** if the primary candidate fails (e.g., HTTP 429/5xx or timeout), automatic failover may send the same prompt to the next allowed candidate across enabled providers (`openrouter`, `kilo`, `ovh`, `llm7`). Pass `--no-fallback` or `-p <provider>` to restrict traffic to a single model or provider.
- **Default-deny content gate:** before any network request, the router requires an allowlisted `--task-class` and scans the prompt for code, config, and private infrastructure. A hit returns `BYPASS_TO_PRIMARY` (exit `0`, no reply). A false positive stays on the primary model.
- **Partial credential filter (DLP):** blocks common secret/token formats locally **before** any network request (`Exit code: 3`); it is a safety net, not a confidentiality guarantee. A DLP hit during `--check-policy` is `BYPASS_TO_PRIMARY` with `dlp_blocked` true (exit `0`); a real call still exits `3`.
- **Audit logs & honest cost reporting:** local audit logs record metadata only by default (opt-in 60-char preview via `OR_FREE_AUDIT_PREVIEW=1`). When an upstream response omits cost, `or-free` reports `"cost_usd": null` with a `billing_policy` label — never a fabricated `0.0`.

---

## Package Integrity Verification (`SHA256SUMS.txt`)

To verify the tracked files listed in `SHA256SUMS.txt` — including `scripts/or_free.py` and `SKILL.md` — match this release byte-for-byte:

```bash
cd "$HOME/agent-data/workflows/free-llm-router" && sha256sum -c SHA256SUMS.txt
```

---

## Uninstall

1. Remove any scheduled task (`crontab -e`, `launchctl unload`, or `schtasks /Delete /TN "or-free-daily-scan"`).
2. Delete the state directory (`~/.config/or-free`, `$OR_FREE_HOME`, or `%LOCALAPPDATA%\or-free`).
3. Remove the `free-llm-router` skill folder (and the `or_free_task_pools` key in `~/.ob1/config.json` if `--sync-ob1` was used).

## License

MIT — see [LICENSE](./LICENSE).
