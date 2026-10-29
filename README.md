# Free LLM Router (`or-free`): OpenRouter and Multi-Provider Catalog Scanner

Save primary-agent tokens and reduce LLM API costs by routing **non-sensitive** coding, summarization, and brainstorming subtasks to free LLMs (OpenRouter `:free`, Kilo, OVH, LLM7) from **Grok Bot, Cursor, Claude Code, Codex**, or any Python agent — stdlib-only, with a multi-provider free-model catalog scanner.

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
| **Grok Bot** | — | `/home/box/agent-data/workflows/free-llm-router/` | Extract archive into `/home/box/agent-data/workflows/free-llm-router/`, **or** ask Grok Bot to register/update the skill with `UpdateSkill`. If `UpdateSkill` assigns an ID like `free-llm-router-2`, loading the skill reveals the exact `Skill directory:` path. |
| **Cursor** | `.cursor/skills/free-llm-router/` | `~/.cursor/skills/free-llm-router/` | Keep `SKILL.md` and `scripts/` together inside the folder. |
| **Claude Code / Codex** | `.claude/skills/free-llm-router/` | `~/.claude/skills/free-llm-router/` | Works with read-only skill mounts (`chmod -R a-w`). |

### Step 2: Confirm discovery & resolve `SKILL_DIR`

Because agents run shell commands from `/workspace` or your project root (not inside the skill folder), running a relative `python3 scripts/or_free.py` from `/workspace` will fail with `FileNotFoundError`.
- In **Grok Bot** (or Claude Code / Cursor), load `Skill(name="free-llm-router")` (or type `/free-llm-router` in chat) and read the `Skill directory:` path returned by the tool.
- Set `SKILL_DIR` to that absolute path and **always quote paths** so spaces on macOS/Windows never break commands.

### Step 3: Verify status, refresh live catalog, and run your first keyless prompt

**Linux / macOS (`bash` / `zsh` — single-line commands):**

```bash
SKILL_DIR="/home/box/agent-data/workflows/free-llm-router"
python3 "$SKILL_DIR/scripts/or_free.py" --status
python3 "$SKILL_DIR/scripts/scan_and_sync_free_models.py" --apply
python3 "$SKILL_DIR/scripts/or_free.py" -t fast --max-tokens 64 "Say hello in 5 words"
```

**Expected first-run behavior & cold-start troubleshooting:**
- `--status` prints the loaded registry path, snapshot `updated_at` timestamp, security gates, and provider readiness (`kilo` / `llm7` / `ovh` `READY` in keyless mode; `openrouter` `SKIP (waiting_for_key)` until an OpenRouter key is configured) and exits with **`Exit code: 0`**.
  - *What `SKIP (waiting_for_key)` means:* normal in 0-key mode; the router automatically skips OpenRouter and routes through keyless `kilo`, `ovh`, and `llm7`.
  - *Important:* `--status` succeeding (`Exit code: 0`) only confirms local config and registry snapshot loading — it does **not** test live upstream network reachability.
- `scan_and_sync_free_models.py --apply` fetches live provider catalogs, runs `"42"` health probes, and writes a fresh registry snapshot to `$OR_FREE_HOME/or_free_models_registry.json` (never modifying `$SKILL_DIR`). Running `--apply` right after install is recommended because the bundled seed (`scripts/or_free_models_registry.json`) ages over time.
- `-t fast --max-tokens 64 "Say hello in 5 words"` routes to a live keyless model, prints a short greeting (e.g. `Hello there, how are you?`), and exits with **`Exit code: 0`**.
  - *If `-t fast` returns `Exit code: 1` (all keyless candidates temporarily rate-limited or unreachable):* (1) run `scan_and_sync_free_models.py --apply` to refresh live pool availability, (2) retry after 30–60s, or (3) set a free-tier `OPENROUTER_API_KEY` to unlock additional `$0/$0` `:free` models.

**Windows PowerShell (single-line commands, quoted for paths with spaces):**

```powershell
py -3 "C:\Users\Alice Smith\skills\free-llm-router\scripts\or_free.py" --status
py -3 "C:\Users\Alice Smith\skills\free-llm-router\scripts\scan_and_sync_free_models.py" --apply
py -3 "C:\Users\Alice Smith\skills\free-llm-router\scripts\or_free.py" -t fast --max-tokens 64 "Say hello in 5 words"
```

**Windows CMD (`cmd.exe`):**

```bat
set "SKILL_DIR=C:\Users\Alice Smith\skills\free-llm-router"
py -3 "%SKILL_DIR%\scripts\or_free.py" --status
py -3 "%SKILL_DIR%\scripts\scan_and_sync_free_models.py" --apply
py -3 "%SKILL_DIR%\scripts\or_free.py" -t fast --max-tokens 64 "Say hello in 5 words"
```

Examples: [examples/README.md](examples/README.md) · Policy details: [references/provider-and-registry-policy.md](references/provider-and-registry-policy.md)

---

## What tasks should I offload? (Quick FAQ)

| Suitable (Good for) | Avoid (Bad for) |
|---|---|
| Short boilerplate / utility functions with doctests | Secrets, API keys, customer PII, proprietary source |
| Public changelog / docs / log summarization | Multi-step debugging that needs your private repo state |
| Format conversion (JSON ↔ CSV schema, regex drafts) | Final architecture decisions or production config changes |
| Brainstorming options / naming / test-case ideas | Anything you cannot send to a public free endpoint |

Rule of thumb: if the subtask is self-contained, non-sensitive, and the primary agent can verify a short answer without regenerating it, offload it. Otherwise keep it on the primary agent.

## How do I actually save primary-agent tokens with `or-free`?

Simply running a free model does not automatically save primary-agent tokens if the agent sends huge prompts, reads back multi-page outputs, or regenerates the answer itself. Follow this delegation contract:

1. **Delegate self-contained, non-sensitive subtasks:** boilerplate functions, unit-test stubs, public documentation/log summaries, regex/schema conversions, or brainstorming.
2. **Send minimal input & cap output (`--max-tokens`):** pass only the required public context and set `--max-tokens 400`–`800` so the tool output returned into the primary agent's context window stays compact.
3. **Verify exit `0` and stop:** once `or_free.py` returns `Exit code: 0`, the primary agent performs a brief sanity check and uses the output directly **without regenerating the same text**.
4. **Measure honestly:** the `usage` object in `--json` output reports **offloaded free-endpoint tokens**, not net primary-agent tokens saved (since the primary agent still spends tokens invoking the tool and reading the result).

See [examples/README.md](examples/README.md) for a complete end-to-end **Grok Bot Token-Saving Workflow Example**.

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
| `OR_FREE_HOME` | Optional override for config/state/log directory (defaults: `~/.config/or-free` on Linux/macOS, `%LOCALAPPDATA%\or-free` on Windows) |
| `OR_FREE_REGISTRY_PATH` | **Strict** registry path (overridden by `--registry`; exits with code `4` on missing/invalid/stale file) |
| `OR_FREE_MAX_REGISTRY_AGE_HOURS` | Reject catalog price proofs older than `<hours>` (finite `> 0`) |
| `XDG_CONFIG_HOME` | Linux/macOS config root (`$XDG_CONFIG_HOME/or-free` when `OR_FREE_HOME` is unset) |
| `LOCALAPPDATA` | Windows config root (`%LOCALAPPDATA%\or-free` when `OR_FREE_HOME` is unset) |
| `OR_FREE_AUDIT_PREVIEW=1` | Opt-in 60-char prompt/reply previews in local audit logs |
| `NVIDIA_API_KEY` / `ROUTEOPEN_API_KEY` | Used only when `--allow-unverified-model` is explicitly passed |

### Exit Codes

| Code | Meaning |
|---:|---|
| `0` | **Success:** route candidate returned a valid response (or `--status` / `--validate-registry` succeeded). |
| `1` | **Candidates exhausted or runtime/lock error:** all allowed route candidates failed/timed out, general runtime error, or state lock/persistence failure. |
| `2` | **Blocked by zero-cost policy gate:** requested model/provider failed `is_safe_free_target` (paid model, missing `$0/$0` catalog proof, or unverified provider without `--allow-unverified-model`). |
| `3` | **Blocked by local DLP filter:** prompt matched the partial credential filter before any network request was sent. |
| `4` | **Strict registry or TTL error:** strict registry file (`--registry` or `OR_FREE_REGISTRY_PATH`) is missing, invalid, or older than `--max-registry-age-hours`, or `--max-registry-age-hours` value is invalid. |

Registry load order (never loaded from CWD): `--registry` (strict) → `OR_FREE_REGISTRY_PATH` (strict) → `$OR_FREE_HOME/or_free_models_registry.json` → bundled `scripts/or_free_models_registry.json`.

---

## How do I refresh free models and schedule daily scans?

### Standard user scan commands (safe for read-only skill installs)

```bash
SKILL_DIR="/home/box/agent-data/workflows/free-llm-router"
python3 "$SKILL_DIR/scripts/scan_and_sync_free_models.py" --apply
python3 "$SKILL_DIR/scripts/scan_and_sync_free_models.py" --apply --json
python3 "$SKILL_DIR/scripts/or_free.py" --validate-registry "$HOME/.config/or-free/or_free_models_registry.json"
```

`--apply` writes only to `$OR_FREE_HOME` by default (`$OR_FREE_HOME/or_free_models_registry.json` and `$OR_FREE_HOME/free_model_scan_latest.md`). Optional flags include `--output-dir <dir>` and `--sync-ob1` *(optional OB-1 local router integration that merges `or_free_task_pools` into `~/.ob1/config.json`; most users can ignore)*.

Registry writes are atomic and locked, with per-run backups and rollback on failure. See [references/provider-and-registry-policy.md](references/provider-and-registry-policy.md).

### Scheduled daily scan (always use full absolute paths for Python and the script)

- **Linux (`cron`):**
  ```cron
  15 4 * * * /usr/bin/python3 "/home/box/agent-data/workflows/free-llm-router/scripts/scan_and_sync_free_models.py" --apply >/dev/null 2>&1
  ```
- **macOS (`launchd`):** in `~/Library/LaunchAgents/com.or-free.scanner.plist`, set `ProgramArguments` to `["/usr/bin/python3", "/Users/<you>/.claude/skills/free-llm-router/scripts/scan_and_sync_free_models.py", "--apply"]` with `StartCalendarInterval` (`Hour=4`, `Minute=15`), then load with `launchctl load ~/Library/LaunchAgents/com.or-free.scanner.plist`.
- **Windows (`schtasks` — CMD / PowerShell):**
  ```bat
  schtasks /Create /SC DAILY /TN "or-free-daily-scan" /TR ""C:\Python311\python.exe" "C:\Users\Alice Smith\skills\free-llm-router\scripts\scan_and_sync_free_models.py" --apply" /ST 04:15
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
- **Partial credential filter (DLP):** blocks common secret/token formats locally **before** any network request (`Exit code: 3`); it is a safety net, not a confidentiality guarantee.
- **Audit logs & honest cost reporting:** local audit logs record metadata only by default (opt-in 60-char preview via `OR_FREE_AUDIT_PREVIEW=1`). When an upstream response omits cost, `or-free` reports `"cost_usd": null` with a `billing_policy` label — never a fabricated `0.0`.

---

## Package Integrity Verification (`SHA256SUMS.txt`)

To verify that `scripts/or_free.py`, `scripts/scan_and_sync_free_models.py`, and the bundled seed `scripts/or_free_models_registry.json` match the audited R7 release byte-for-byte:

```bash
cd "/home/box/agent-data/workflows/free-llm-router" && sha256sum -c SHA256SUMS.txt
```

---

## Uninstall

1. Remove any scheduled task (`crontab -e`, `launchctl unload`, or `schtasks /Delete /TN "or-free-daily-scan"`).
2. Delete the state directory (`~/.config/or-free`, `$OR_FREE_HOME`, or `%LOCALAPPDATA%\or-free`).
3. Remove the `free-llm-router` skill folder (and the `or_free_task_pools` key in `~/.ob1/config.json` if `--sync-ob1` was used).

## License

MIT — see [LICENSE](./LICENSE).
