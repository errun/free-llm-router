# Provider and Registry Policy

Detailed behavior behind `or-free` (`scripts/or_free.py`, `scripts/scan_and_sync_free_models.py`). Providers can change pricing; these are best-effort local checks, not a guarantee.

## Pricing proof nuance

- **OpenRouter & Kilo:** a model is routable only if its ID ends in `:free` (or a known free alias) **and** it is in registry `pricing_verified_models` with `prompt_usd == "0"` and `completion_usd == "0"` (`source`: `openrouter_catalog_zero` / `kilo_catalog_zero`). Bare `:free` is not enough.
- **Saved snapshot vs. live scan:** normal `or_free.py` invocations read the saved registry snapshot (`$OR_FREE_HOME/or_free_models_registry.json`, or bundled `scripts/or_free_models_registry.json` on first run) and do **not** re-fetch live catalogs per prompt. Run `scan_and_sync_free_models.py --apply` to refresh catalog proofs.
- When a registry is loaded, an empty or TTL-expired `pricing_verified_models` **never** restores built-in catalog seeds.
- **OVH & LLM7:** curated keyless allowlist (`curated_keyless_allowlist_models`). Vendor catalogs often use rate-card stubs, so no catalog `$0/$0` is claimed.
- **NVIDIA / RouteOpen:** default denied; `--allow-unverified-model` prints a warning and cost is not guaranteed.
- `task_pools` cannot self-certify: every pool member must pass `is_safe_free_target` against the verified/allowlist sets.
- Live catalog `context_length` overrides static metadata for the `long` (`>=200K`) pool.
- Anti-poisoning: model ID regex `^[a-zA-Z0-9._:/-]+$` (≤120 chars); classifiers and tiny `<7B` models (`TINY_MODEL_RE`) excluded.
- Transport & failover: HTTPS host whitelist; redirects raise `HTTPError`; 4xx/5xx never parsed as success. Automatic failover may send the prompt to the next allowed provider on transient failure unless `--no-fallback` or `-p <provider>` is set.

## TTL / freshness

- `OR_FREE_MAX_REGISTRY_AGE_HOURS` or `--max-registry-age-hours` (finite `> 0`) rejects stale price proofs; strict registries fail with exit `4`.
- Registry is loaded once in `main()` after CLI flags and TTL are parsed; never auto-loaded from CWD.

## Probes and budget lock

- Probe reply must equal `"42"` after `strip().rstrip(".")`.
- Router and scanner probes share `RouterStateLock` (`fcntl` / `msvcrt`); if the lock or state persistence fails, HTTP is blocked (exit `1`).
- **Budget skip vs 429:** local probe budget exhaustion reports `probe_state=skipped_budget` / `status=null`, counted separately from real HTTP 429 standby.

## RegistryUpdateLock, atomic write, backup and rollback

Registry commits use a **separate** `RegistryUpdateLock` (not the request-budget `RouterStateLock`):

1. Deduplicate resolved `Path` targets.
2. Write a unique `.candidate.{nonce}.json` and validate it (Gate 5, `--validate-registry`).
3. Prepare **new** unique backups for existing targets.
4. Write unique `.tmp.{nonce}` (O_EXCL, `0o600`; preserve POSIX mode, default `0o600`) and `os.replace` onto production.
5. After success, prune unique `.bak.*` to the last 3.

Rollback:

- Restores **only** targets confirmed replaced this run, from **this run's** unique backup.
- Newly created targets (`existed_before == False`) are unlinked.
- If backup prep fails before any replace, production is untouched; a pre-existing stale fixed `.bak` is never copied onto production.

## Other write targets

- `--update-bundled-seed` *(maintainer-only packaging flag)* rewrites `scripts/or_free_models_registry.json` (needs writable skill dir; install dir may be read-only otherwise).
- `--sync-ob1` *(optional OB-1 local router integration; most users can ignore)* non-destructively merges `or_free_task_pools` into `~/.ob1/config.json`; refuses a corrupt/non-object file.
