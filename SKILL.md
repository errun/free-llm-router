---
name: free-llm-router
description: >-
  Use when a user wants free AI models, cheap LLM alternatives, cost-free text
  generation, to save primary-agent tokens or reduce LLM API costs by offloading
  explicitly tagged public tasks (public research/copy, brainstorming, generic
  knowledge, sanitized data) to free LLMs — default-deny, never company code, find
  OpenRouter :free models, refresh a multi-provider free-model registry, set up
  free-model routing for Grok Bot, Codex, Cursor, or Claude Code, or check the
  Grok Ultra quota policy gate (--check-policy) before offloading. Provides a
  stdlib-only Python router (`or-free` / scripts/or_free.py) and catalog scanner
  with zero-price catalog checks, curated keyless allowlists, a quota policy
  gate with automatic cycle renewal, and a partial credential filter (DLP).
  Pricing and availability can change; zero billing is not guaranteed.
---
# Free LLM Router (`or-free`)

**Prerequisites:** Python 3.9+ (standard library only; no `pip install` required).

## When to use

- **Save primary-agent tokens / reduce API cost:** offload self-contained subtasks that carry an **allowlisted `--task-class`** (public research, public copy, brainstorming, general knowledge, sanitized data) to free LLMs. **Default-deny:** anything untagged, any coding task, and anything containing company code/config/architecture stays on the primary model.
- **Discover & verify `:free` models:** check which OpenRouter `:free` and Kilo models currently pass `$0/$0` catalog checks, plus curated keyless routes (OVH, LLM7).
- **Refresh the free-model registry:** run live catalog scans and `"42"` health probes into `$OR_FREE_HOME`.
- **Configure agent routing:** set up free-model delegation for **Grok Bot, Codex, Cursor, or Claude Code** (note: automatic semantic skill triggering depends on the host agent; you can also load `/free-llm-router` or `Skill(name="free-llm-router")` explicitly).

## Agent Execution Contract (Read Before Running Commands)

1. **Always resolve `SKILL_DIR` to an absolute path first:**
   - Agents (including Grok Bot, Cursor, and Claude Code) typically execute shell commands from `/workspace` or the user's project root — **not** inside the skill folder. Running a bare `python3 scripts/or_free.py` from `/workspace` will fail with `FileNotFoundError`.
   - Resolve `SKILL_DIR` from the `Skill directory:` header shown when this `SKILL.md` is loaded (for example `/home/box/agent-data/workflows/free-llm-router` or `/home/box/agent-data/workflows/free-llm-router-2`), and **always quote paths** (`python3 "$SKILL_DIR/scripts/or_free.py"`).
   - Copying the skill folder does **not** install a global `or-free` binary on `PATH`.
2. **Token-Saving Delegation Contract:**
   - **What to delegate (default-deny):** only subtasks you can tag with an allowed `--task-class`: `public_research` (public news/docs/open-source/competitor public info), `public_copy` (marketing copy, store descriptions, tweets, translation/polish of text meant to be public), `brainstorm` (names, outlines, idea lists), `general_knowledge` (generic explanations without our code), `sanitized_data` (already de-identified logs/data); legacy `public_summary`, `sanitized_log_preprocessing`, `public_text_classification`, `public_document_extraction` also allowed. **Never delegate** company source code, configs, infra/architecture, internal hosts/IPs/paths, or anything untagged — those return `BYPASS_TO_PRIMARY`.
   - **Minimal input + bounded output:** pass only the necessary non-sensitive context and cap output length with `--max-tokens 600` (or similar budget) so reading the tool result does not bloat the primary agent's context window.
   - **Verify & stop:** check that exit code is `0`, inspect the returned text/JSON, and use or present the verified result **without regenerating the same answer in the primary agent**.
   - **Honest token accounting:** the `usage` field returned by `--json` measures **offloaded free-endpoint tokens**, **not** net primary-agent tokens saved (the primary agent still spends tokens constructing the command and reading the result). Never claim unmeasured percentage savings.

## Grok Bot 3-Step Install & Discovery

1. **Step 1 — Install skill files:** Extract `free-llm-router/` into `/home/box/agent-data/workflows/free-llm-router/` (or ask Grok Bot to register/update the skill via `UpdateSkill`; if `UpdateSkill` assigns a suffixed folder such as `free-llm-router-2`, the `Skill` tool will report the exact `Skill directory:` path when loaded).
   - *Other hosts:* Cursor uses `.cursor/skills/free-llm-router/` (project) or `~/.cursor/skills/free-llm-router/` (user); Claude Code / Codex uses `.claude/skills/free-llm-router/` (project) or `~/.claude/skills/free-llm-router/` (user).
2. **Step 2 — Confirm discovery & set `SKILL_DIR`:** Load `Skill(name="free-llm-router")` (or type `/free-llm-router` in chat) and copy the `Skill directory:` path into `SKILL_DIR`.
3. **Step 3 — Verify local status, refresh live catalog, and run a keyless test:**
   ```bash
   SKILL_DIR="/home/box/agent-data/workflows/free-llm-router"
   python3 "$SKILL_DIR/scripts/or_free.py" --status
   python3 "$SKILL_DIR/scripts/scan_and_sync_free_models.py" --apply
   python3 "$SKILL_DIR/scripts/or_free.py" -t fast --task-class general_knowledge --max-tokens 64 "Say hello in 5 words"
   ```
   *Note:* `--status` (`Exit code: 0`) verifies local config/registry loading only, not live network reachability. `openrouter=SKIP (waiting_for_key)` is normal in keyless mode (`kilo`, `ovh`, `llm7` remain `READY`). If `-t fast` fails with `Exit code: 1` (all keyless candidates rate-limited/exhausted), run `scan_and_sync_free_models.py --apply` to refresh `$OR_FREE_HOME` or add a free-tier `OPENROUTER_API_KEY`.

## Boundaries & Privacy

- **Default-deny (owner policy):** free models only receive prompts tagged with an allowlisted `--task-class`. The router also runs a local **code/config/infra scanner on every prompt, even allowlisted ones**: code fences, source-like lines (`def/class/function/import/#include/using/public/private`, `; { }`), JSON/YAML/INI/.env blocks, nginx/Docker/k8s/ssh config, SQL, stack traces, IPv4/IPv6, internal hostnames (`localhost`, `*.local/.internal/.corp`, `dev-/staging-/jenkins.` …), local paths (`/Users/`, `C:\`, `/etc/`, `~/` …), source/config filenames, connection strings, `host:port`, and non-public URLs → `BYPASS_TO_PRIMARY` with `reason=content_scan_code_or_infra:<rule>`. It deliberately over-blocks; a false positive just means the primary model does the task. Add company domains with `OR_FREE_INTERNAL_DOMAINS=example.com,studio.io`.
- **No secrets or proprietary code:** never send API keys, credentials, customer PII, or confidential source code. Public free endpoints may log prompts and completions.
- **Multi-provider failover notice:** if the first candidate fails, automatic failover may send your prompt to the next allowed provider (`openrouter`, `kilo`, `ovh`, `llm7`) unless you restrict routing with `--no-fallback` or `-p <provider>`.
- **Partial DLP only:** the local credential filter (`Exit code: 3`) catches common token patterns before network I/O; it is **not** a confidentiality guarantee.
- **Best-effort zero-cost checks:** normal `or_free.py` calls read the saved registry snapshot (`$OR_FREE_HOME/or_free_models_registry.json`, falling back to bundled `scripts/or_free_models_registry.json` on first run) rather than re-fetching provider catalogs on every prompt. Providers can change pricing at any time, and free pool capability fluctuates. Use `--max-registry-age-hours 24` (or `OR_FREE_MAX_REGISTRY_AGE_HOURS=24`) to enforce a hard freshness TTL.
- **Unverified providers off by default:** NVIDIA and RouteOpen are blocked unless `--allow-unverified-model` is explicitly passed.

## Identity & State Directories

- Skill name: `free-llm-router` → CLI brand: `or-free` → Entry script: `"$SKILL_DIR/scripts/or_free.py"`.
- Default state directory (no need to set `OR_FREE_HOME` before first run):
  - **Linux / macOS:** `~/.config/or-free` (or `$XDG_CONFIG_HOME/or-free`)
  - **Windows:** `%LOCALAPPDATA%\\or-free`
  - **Optional override:** set `OR_FREE_HOME="/custom/path"`

## Single-Line Commands (Copy-Paste Ready)

```bash
SKILL_DIR="/home/box/agent-data/workflows/free-llm-router"
python3 "$SKILL_DIR/scripts/or_free.py" --status
python3 "$SKILL_DIR/scripts/or_free.py" --check-policy --json --task-class public_copy "Write a tweet announcing our cat puzzle game"
python3 "$SKILL_DIR/scripts/or_free.py" --check-policy --json --task-class public_research --prompt-file ./prompt.txt
python3 "$SKILL_DIR/scripts/or_free.py" -t fast --task-class general_knowledge --max-tokens 64 "Say hello in 5 words"
python3 "$SKILL_DIR/scripts/or_free.py" --caller "GrokBot" -t general --task-class brainstorm --max-tokens 400 "10 name ideas for a cozy cat puzzle game"
python3 "$SKILL_DIR/scripts/or_free.py" -t general --task-class public_summary --max-tokens 400 "Summarize this public changelog in 5 bullets: ..."
python3 "$SKILL_DIR/scripts/or_free.py" -t long --task-class public_document_extraction --max-tokens 800 "Extract key dates from this public document: ..."
# Coding tasks (-t code / --task-class code) and untagged prompts => BYPASS_TO_PRIMARY: do them on the primary model.
```

Windows PowerShell (`py -3`):
```powershell
$SKILL_DIR = "C:\\path\\to\\free-llm-router"
py -3 "$SKILL_DIR\\scripts\\or_free.py" --status
py -3 "$SKILL_DIR\\scripts\\or_free.py" -t fast --task-class general_knowledge --max-tokens 64 "Say hello in 5 words"
```

## Refresh Registry (User vs Maintainer)

User commands (write only to `$OR_FREE_HOME`; `$SKILL_DIR` can be read-only):
```bash
SKILL_DIR="/home/box/agent-data/workflows/free-llm-router"
python3 "$SKILL_DIR/scripts/scan_and_sync_free_models.py" --apply
python3 "$SKILL_DIR/scripts/scan_and_sync_free_models.py" --apply --json
python3 "$SKILL_DIR/scripts/or_free.py" --validate-registry "$HOME/.config/or-free/or_free_models_registry.json"
```

## Quota Policy Gate (Grok Ultra dynamic split)

Before every call, `or_free.py` first applies the **default-deny content gate** (task-class allowlist + DLP + code/config scanner), then reads `quota_policy.json` (path: `OR_FREE_QUOTA_POLICY_PATH`, else `$OR_FREE_HOME/quota_policy.json`) to decide the quota split. The content gate applies even with no policy file: untagged/code/non-allowlisted → `BYPASS_TO_PRIMARY`. Only content that passes the gate and has no policy file gets `POLICY_UNAVAILABLE` (free pool allowed).

- **Check first:** `python3 "$SKILL_DIR/scripts/or_free.py" --check-policy --json --task-class <class> "<prompt>"` (or `--prompt-file FILE|-`; always exit 0). Without a prompt the content scanner is skipped (`content_scanned=false`); the real call always scans. Read the `decision` field:
  - `USE_FREE` → offload; `BYPASS_TO_PRIMARY` → do it on the primary model; `POLICY_UNAVAILABLE` → offload normally (content gate already passed).
  - New fields: `content_gate` (`pass`/`deny`), `content_scanned`, `content_scan_hit`, `dlp_blocked`/`blocked` (DLP hit in `--check-policy` → `BYPASS_TO_PRIMARY`, exit 0; the real call still exits 3), `allowed_task_classes`, `policy_allowlist_ignored`.
- **BYPASS caveat:** when a normal call is gated to `BYPASS_TO_PRIMARY` (no/denied task class, code/config content, quota tier), it prints one decision JSON line, sends no request, and **exits 0 with no model reply**. Callers must check `decision`, not only the exit code.
- **Tiers by β** (remaining quota after ~5% reserve ÷ remaining time fraction): green 0% free (use Ultra before it expires), blue 35%, yellow 65%, red 90% (`routing.free_target`).
- **Auto-renewal:** if `cycle.reset_at` has passed, the policy rolls to the cycle containing now (same length, default 7 days), sets `usage.status=unknown`, tier blue, `revision+1`, backs up `quota_policy.json.pre-rollover.<rev>`, and atomically writes back (0 tokens, no cron). `OR_FREE_POLICY_NO_ROLLOVER=1` renews in memory only.
- **Usage unknown** → conservative blue (35%) `USE_FREE`, never a blanket bypass. Write `usage.status=known` + `used_fraction` to restore exact β tiers.
- **Always bypass (default-deny):** missing `--task-class`, `auto`/`general`/`code`/`long`/`fast`, any class not in the allowlist, denylisted classes (restricted_data, security_sensitive_code, complex_reasoning, production_change_plan, high_impact_final_decision, verified_low_risk_code_candidate, code), `--sensitivity` other than `public`/`sanitized`/`deidentified`, and any prompt the code/config/infra scanner flags. **Allowlist (hardcoded; `routing.free_allowlist` can only narrow it, never widen it):** public_research, public_copy, brainstorm, general_knowledge, sanitized_data, public_summary, sanitized_log_preprocessing, public_text_classification, public_document_extraction.
- Other flags: `--estimated-tokens N`, `--green-batch-exception`, `--policy-path PATH`.
- Status values: `fresh`, `stale_same_cycle`, `usage_unknown`, `cycle_expired` (only when renewal is disabled/impossible), `policy_unavailable`.
- Policy JSON: required `cycle{starts_at, reset_at, cycle_id}`, `usage{status, used_fraction, observed_at, max_age_seconds}`, `runtime{tier}`; optional `revision`, `valid_until`, `writer_id`, `thresholds{...}`, `routing{free_allowlist, free_denylist, free_target}`, `rollover{...}`.

## Exit Codes

- `0`: Success (response printed to stdout; check `http_status == 200` and `content` in `--json` mode). Also returned by `--check-policy` and by content-gate or quota-gated `BYPASS_TO_PRIMARY` (decision JSON only, no reply).
- `1`: All route candidates failed/exhausted, general runtime error, or state lock/persistence failure.
- `2`: Target model blocked by zero-cost policy gate (`is_safe_free_target` failed: paid model, missing catalog `$0/$0` proof, or unverified provider without `--allow-unverified-model`).
- `3`: Prompt blocked by local partial credential filter (DLP).
- `4`: Strict registry file missing/invalid/stale or invalid `--max-registry-age-hours`.

## More

- Runnable workflows & Grok Bot token-saving example: [examples/README.md](examples/README.md)
- Pricing proof, registry locking, backup/rollback, TTL: [references/provider-and-registry-policy.md](references/provider-and-registry-policy.md)
- Full user guide & integrity check (`SHA256SUMS.txt`): [README.md](README.md)
