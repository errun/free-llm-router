#!/usr/bin/env python3
"""
or_free.py / or-free — Zero-Cost Multi-Provider Free LLM Router (stdlib-only)

Hard guarantees (best-effort local enforcement; provider catalogs can change):
1. ZERO-BILLING LOCK (exit 2): Never route to unverified/paid models.
   - openrouter: requires ":free" suffix or openrouter/free (+ registry $0 when present)
   - kilo: requires ":free"/known free IDs or verified registry allowlist
   - ovh/llm7: keyless public endpoints; only verified allowlist/registry (or --allow-unverified-model)
   - nvidia/routeopen: disabled by default for strangers (opt-in + verified free + key)
2. DLP (exit 3): Block private keys, API tokens, bearer tokens, password assignments,
   and [SECRET]/[涉密] markers before any external HTTP request.
3. Portable paths: OR_FREE_HOME / XDG_CONFIG_HOME / ~/.config/or-free (no hardcoded
   machine-specific absolute home literals). Bundled registry beside this script is the seed.
4. Proactive watermarks + 24h audit metadata logs (optional --audit-preview for text previews).
"""

import argparse
from datetime import datetime, date, timedelta, timezone
import json
import math
import os
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
SCRIPT_DIR = Path(__file__).resolve().parent
WHITELISTED_DOMAINS = frozenset({
    "openrouter.ai",
    "api.kilo.ai",
    "api.llm7.io",
    "oai.endpoints.kepler.ai.cloud.ovh.net",
    "hashneuron.space",
    "integrate.api.nvidia.com",
})
# Verified $0 / keyless allowlist for non-OpenRouter platforms (best-effort; scanner refreshes registry)
VERIFIED_FREE_ALLOWLIST = frozenset({
    "kilo/reflection/beam-free",
    "kilo/kilo-auto/free",
    "kilo/openrouter/free",
    "kilo/dots-studio/dots-3-note-preview:free",
    "kilo/nvidia/nemotron-3.5-super-vl:free",
    "kilo/poolside/laguna-s-2.1:free",
    "kilo/poolside/laguna-xs-2.1:free",
    "kilo/recursive/flint-v1.0:free",
    "kilo/nvidia/nemotron-3.5-lightning:free",
    "kilo/cohere/north-mini-code:free",
    "kilo/nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    "kilo/nvidia/nemotron-3-ultra-550b-a55b:free",
    "kilo/xingchen-agi/xing4.0-29b-a4b:free",
    "ovh/Qwen3.6-27B",
    "ovh/Qwen3.5-397B-A17B",
    "ovh/gpt-oss-120b",
    "ovh/Meta-Llama-3_3-70B-Instruct",
    "ovh/Qwen2.5-VL-72B-Instruct",
    "llm7/minimax-m2.7",
    "llm7/codestral-latest",
    "llm7/nemotron-3-nano:30b",
    "llm7/gpt-oss:20b",
})
# Runtime set populated from loaded registry (verified free model IDs)
_REGISTRY_VERIFIED_IDS: set = set()
_REGISTRY_LOADED_FROM: Optional[str] = None
_REGISTRY_UPDATED_AT: Optional[str] = None
_ALLOW_UNVERIFIED_MODEL = False
_STRICT_REGISTRY_MODE = False
_EXPLICIT_REGISTRY_PATH: Optional[str] = None
_MAX_REGISTRY_AGE_HOURS: Optional[float] = None
_REGISTRY_INITIALIZED: bool = False
_INPROC_STATE_LOCK = threading.RLock()
MAX_REGISTRY_AGE_HOURS_DEFAULT_WARN = 168.0  # 7 days

# Populated from registry pricing_verified_models (prompt_usd=="0" & completion_usd=="0")
VERIFIED_ZERO_PRICE_IDS: set = set()
# Populated from curated_keyless_allowlist_models ∩ KEYLESS_ALLOWLIST
CURATED_KEYLESS_IDS: set = set()
# Built-in seed used ONLY when no registry is loaded (bundled cold-start). Must be catalog-proven at last publish.
BUILTIN_CATALOG_ZERO_IDS = frozenset({
    "kilo/reflection/beam-free",
    "kilo/kilo-auto/free",
    "kilo/openrouter/free",
    "kilo/dots-studio/dots-3-note-preview:free",
    "kilo/nvidia/nemotron-3.5-super-vl:free",
    "kilo/poolside/laguna-s-2.1:free",
    "kilo/poolside/laguna-xs-2.1:free",
    "kilo/recursive/flint-v1.0:free",
    "kilo/nvidia/nemotron-3.5-lightning:free",
    "kilo/cohere/north-mini-code:free",
    "kilo/nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    "kilo/nvidia/nemotron-3-ultra-550b-a55b:free",
    "kilo/xingchen-agi/xing4.0-29b-a4b:free",
})
KEYLESS_ALLOWLIST = frozenset({
    "ovh/Qwen3.6-27B",
    "ovh/Qwen3.5-397B-A17B",
    "ovh/gpt-oss-120b",
    "ovh/Meta-Llama-3_3-70B-Instruct",
    "ovh/Qwen2.5-VL-72B-Instruct",
    "llm7/minimax-m2.7",
    "llm7/codestral-latest",
    "llm7/nemotron-3-nano:30b",
    "llm7/gpt-oss:20b",
})
_REGISTRY_SHA256: Optional[str] = None
_REGISTRY_AGE_HOURS: Optional[float] = None

TZ_UTC8 = timezone(timedelta(hours=8))
LOG_RETENTION_DAYS = 30
LOG_DATE_RE = re.compile(r"^usage-(\d{4}-\d{2}-\d{2})\.(jsonl|zip|gz)$")

# Provider definitions (OpenRouter keyed + OB-1 keyless channels)
PROVIDERS: Dict[str, Dict[str, Any]] = {
    "openrouter": {
        "name": "OpenRouter (:free Pool)",
        "base_url": "https://openrouter.ai/api/v1",
        "keyless": False,
        "rpm_limit": 20,
        "rpm_watermark": 14,          # 70% proactive watermark (switch before 20 RPM 429)
        "rpd_limit": 1000,
        "rpd_watermark": 850,         # 85% proactive account watermark (leave 150 buffer)
        "model_rpd_limit": 50,
        "model_rpd_watermark": 40,    # 80% proactive per-model watermark (switch at 40/50)
    },
    "kilo": {
        "name": "OB-1 Kilo Gateway (Keyless)",
        "base_url": "https://api.kilo.ai/api/gateway/v1",
        "keyless": True,
        "rpm_limit": 20,
        "rpm_watermark": 15,
        "rpd_limit": 500,
        "rpd_watermark": 400,
        "model_rpd_limit": 100,
        "model_rpd_watermark": 80,
    },
    "llm7": {
        "name": "OB-1 LLM7 Router (Keyless)",
        "base_url": "https://api.llm7.io/v1",
        "keyless": True,
        "rpm_limit": 40,
        "rpm_watermark": 28,
        "rpd_limit": 1000,
        "rpd_watermark": 800,
        "model_rpd_limit": 300,
        "model_rpd_watermark": 240,
    },
    "ovh": {
        "name": "OB-1 OVH AI Endpoints (Keyless)",
        "base_url": "https://oai.endpoints.kepler.ai.cloud.ovh.net/v1",
        "keyless": True,
        "rpm_limit": 15,
        "rpm_watermark": 11,
        "rpd_limit": 500,
        "rpd_watermark": 400,
        "model_rpd_limit": 100,
        "model_rpd_watermark": 80,
    },
    "nvidia": {
        "name": "NVIDIA NIM Free Endpoints (40+ Free Models)",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "keyless": False,
        "key_env": "NVIDIA_API_KEY",
        "key_file": "nvidia_api_key",
        "rpm_limit": 40,
        "rpm_watermark": 32,
        "rpd_limit": 5000,
        "rpd_watermark": 4000,
        "model_rpd_limit": 1000,
        "model_rpd_watermark": 800,
    },
    "routeopen": {
        "name": "RouteOpen Free Gateway (hashneuron.space)",
        "base_url": "https://hashneuron.space/v1",
        "keyless": False,
        "key_env": "ROUTEOPEN_API_KEY",
        "key_file": "routeopen_api_key",
        "rpm_limit": 20,
        "rpm_watermark": 15,
        "rpd_limit": 500,
        "rpd_watermark": 400,
        "model_rpd_limit": 100,
        "model_rpd_watermark": 80,
    },
}

# Task-specific candidate pools & interleaved rotation schedules.
TASK_POOLS: Dict[str, Dict[str, Any]] = {
    "general": {
        "description": "Verified free pool (keyless Kilo/OVH/LLM7 + OpenRouter :free). NVIDIA/RouteOpen excluded by default.",
        "interleave_schedule": [
            "kilo/dots-studio/dots-3-note-preview:free",
            "kilo/reflection/beam-free",
            "kilo/nvidia/nemotron-3.5-super-vl:free",
            "openrouter/dots-studio/dots-3-note-preview:free",
            "openrouter/nvidia/nemotron-3.5-super-vl:free",
            "kilo/poolside/laguna-s-2.1:free",
            "ovh/Qwen3.6-27B",
            "llm7/minimax-m2.7",
            "ovh/Qwen3.5-397B-A17B",
            "kilo/kilo-auto/free",
        ],
        "fallback_chain": [
            "kilo/dots-studio/dots-3-note-preview:free",
            "kilo/reflection/beam-free",
            "kilo/nvidia/nemotron-3.5-super-vl:free",
            "openrouter/dots-studio/dots-3-note-preview:free",
            "openrouter/thinkingmachines/inkling-small:free",
            "openrouter/thinkingmachines/inkling:free",
            "openrouter/nvidia/nemotron-3.5-super-vl:free",
            "kilo/poolside/laguna-s-2.1:free",
            "kilo/poolside/laguna-xs-2.1:free",
            "openrouter/poolside/laguna-s-2.1:free",
            "ovh/Qwen3.6-27B",
            "llm7/minimax-m2.7",
            "ovh/Qwen3.5-397B-A17B",
            "kilo/nvidia/nemotron-3.5-lightning:free",
            "openrouter/nvidia/nemotron-3.5-lightning:free",
            "llm7/codestral-latest",
            "ovh/gpt-oss-120b",
            "openrouter/cohere/north-mini-code:free",
            "kilo/cohere/north-mini-code:free",
            "llm7/nemotron-3-nano:30b",
            "llm7/gpt-oss:20b",
            "ovh/Meta-Llama-3_3-70B-Instruct",
            "openrouter/openrouter/free",
            "kilo/kilo-auto/free",
        ],
    },
    "code": {
        "description": "Verified free coding-oriented pool (keyless-first).",
        "interleave_schedule": [
            "kilo/reflection/beam-free",
            "kilo/dots-studio/dots-3-note-preview:free",
            "kilo/poolside/laguna-s-2.1:free",
            "openrouter/thinkingmachines/inkling-small:free",
            "openrouter/poolside/laguna-s-2.1:free",
            "llm7/codestral-latest",
            "llm7/minimax-m2.7",
            "ovh/Qwen3.6-27B",
            "kilo/recursive/flint-v1.0:free",
            "openrouter/cohere/north-mini-code:free",
        ],
        "fallback_chain": [
            "kilo/reflection/beam-free",
            "kilo/dots-studio/dots-3-note-preview:free",
            "kilo/poolside/laguna-s-2.1:free",
            "kilo/poolside/laguna-xs-2.1:free",
            "openrouter/thinkingmachines/inkling-small:free",
            "openrouter/thinkingmachines/inkling:free",
            "openrouter/dots-studio/dots-3-note-preview:free",
            "openrouter/poolside/laguna-s-2.1:free",
            "llm7/codestral-latest",
            "llm7/minimax-m2.7",
            "ovh/Qwen3.6-27B",
            "ovh/Qwen3.5-397B-A17B",
            "kilo/cohere/north-mini-code:free",
            "openrouter/cohere/north-mini-code:free",
            "ovh/gpt-oss-120b",
            "openrouter/openrouter/free",
            "kilo/kilo-auto/free",
        ],
    },
    "long": {
        "description": "Verified free long-context candidates (prefer known large-ctx notes).",
        "interleave_schedule": [
            "kilo/reflection/beam-free",
            "openrouter/thinkingmachines/inkling:free",
            "openrouter/thinkingmachines/inkling-small:free",
            "kilo/nvidia/nemotron-3.5-super-vl:free",
            "openrouter/nvidia/nemotron-3.5-super-vl:free",
            "kilo/dots-studio/dots-3-note-preview:free",
            "openrouter/dots-studio/dots-3-note-preview:free",
            "ovh/Qwen3.5-397B-A17B",
            "kilo/nvidia/nemotron-3.5-lightning:free",
            "openrouter/nvidia/nemotron-3.5-lightning:free",
        ],
        "fallback_chain": [
            "kilo/reflection/beam-free",
            "openrouter/thinkingmachines/inkling:free",
            "openrouter/thinkingmachines/inkling-small:free",
            "kilo/nvidia/nemotron-3.5-super-vl:free",
            "openrouter/nvidia/nemotron-3.5-super-vl:free",
            "kilo/dots-studio/dots-3-note-preview:free",
            "openrouter/dots-studio/dots-3-note-preview:free",
            "ovh/Qwen3.5-397B-A17B",
            "kilo/nvidia/nemotron-3.5-lightning:free",
            "openrouter/nvidia/nemotron-3.5-lightning:free",
            "openrouter/cohere/north-mini-code:free",
            "openrouter/openrouter/free",
        ],
    },
    "fast": {
        "description": "Verified free low-latency candidates (keyless-first).",
        "interleave_schedule": [
            "llm7/codestral-latest",
            "llm7/minimax-m2.7",
            "llm7/gpt-oss:20b",
            "llm7/nemotron-3-nano:30b",
            "kilo/dots-studio/dots-3-note-preview:free",
            "kilo/nvidia/nemotron-3.5-lightning:free",
            "openrouter/nvidia/nemotron-3.5-lightning:free",
            "openrouter/nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
            "ovh/Qwen3.6-27B",
            "kilo/reflection/beam-free",
        ],
        "fallback_chain": [
            "llm7/codestral-latest",
            "llm7/minimax-m2.7",
            "llm7/gpt-oss:20b",
            "llm7/nemotron-3-nano:30b",
            "kilo/dots-studio/dots-3-note-preview:free",
            "kilo/nvidia/nemotron-3.5-lightning:free",
            "kilo/nvidia/nemotron-3.5-super-vl:free",
            "openrouter/nvidia/nemotron-3.5-lightning:free",
            "openrouter/nvidia/nemotron-3.5-super-vl:free",
            "openrouter/nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
            "ovh/Qwen3.6-27B",
            "kilo/reflection/beam-free",
            "kilo/poolside/laguna-xs-2.1:free",
            "openrouter/cohere/north-mini-code:free",
            "kilo/cohere/north-mini-code:free",
            "ovh/gpt-oss-120b",
            "openrouter/openrouter/free",
            "kilo/kilo-auto/free",
        ],
    },
}

# ── Portable paths (no machine-specific absolute home literals) ────

def get_app_home() -> Path:
    """Primary config/state directory for or-free (cross-platform)."""
    env = (os.environ.get("OR_FREE_HOME") or "").strip()
    if env:
        p = Path(env).expanduser()
        p.mkdir(parents=True, exist_ok=True)
        return p
    xdg = (os.environ.get("XDG_CONFIG_HOME") or "").strip()
    if xdg:
        p = Path(xdg).expanduser() / "or-free"
        p.mkdir(parents=True, exist_ok=True)
        return p
    if os.name == "nt":
        la = (os.environ.get("LOCALAPPDATA") or "").strip()
        if la:
            p = Path(la) / "or-free"
            p.mkdir(parents=True, exist_ok=True)
            return p
        local = Path.home() / "AppData" / "Local" / "or-free"
        try:
            local.mkdir(parents=True, exist_ok=True)
            return local
        except Exception:
            p = Path.home() / ".config" / "or-free"
            p.mkdir(parents=True, exist_ok=True)
            return p
    p = Path.home() / ".config" / "or-free"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_config_dir() -> Path:
    """Writable config dir (state + logs). Alias of get_app_home()."""
    return get_app_home()


def legacy_read_dirs() -> List[Path]:
    """Legacy read-only fallbacks (relative to Path.home(), never hardcoded box paths)."""
    return [
        Path.home() / ".config" / "openrouter",
        Path.home() / ".ob1",
    ]


def registry_candidate_paths(explicit: Optional[Path] = None) -> List[Path]:
    """Trusted registry locations only. NEVER auto-load from Path.cwd() (CWD poisoning)."""
    paths: List[Path] = []
    if explicit is not None:
        paths.append(Path(explicit))
    env_reg = (os.environ.get("OR_FREE_REGISTRY_PATH") or "").strip()
    if env_reg:
        paths.append(Path(env_reg).expanduser())
    paths.append(get_app_home() / "or_free_models_registry.json")
    paths.append(SCRIPT_DIR / "or_free_models_registry.json")
    # Deduplicate preserving order
    seen = set()
    out: List[Path] = []
    for p in paths:
        try:
            key = str(p.resolve()) if p.exists() else str(p)
        except Exception:
            key = str(p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def _sha256_bytes(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


def _parse_registry_age_hours(raw: dict) -> Optional[float]:
    epoch = raw.get("updated_epoch") if "updated_epoch" in raw else raw.get("verified_epoch")
    if epoch is not None:
        try:
            ep_f = float(epoch)
            if math.isfinite(ep_f) and ep_f > 0:
                return max(0.0, (time.time() - ep_f) / 3600.0)
        except Exception:
            pass
    updated = str(raw.get("updated_at") or "").strip()
    if not updated:
        return None
    try:
        if "UTC+8" in updated:
            core = updated.replace(" UTC+8", "").strip()
            dt = datetime.strptime(core, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ_UTC8)
            return max(0.0, (datetime.now(TZ_UTC8) - dt).total_seconds() / 3600.0)
    except Exception:
        pass
    try:
        dt = datetime.strptime(updated[:10], "%Y-%m-%d").replace(tzinfo=TZ_UTC8)
        return max(0.0, (datetime.now(TZ_UTC8) - dt).total_seconds() / 3600.0)
    except Exception:
        return None


def _extract_pricing_verified_ids(raw: dict, enforce_ttl: bool = False) -> set:
    """ONLY from pricing_verified_models with prompt_usd=='0' and completion_usd=='0' and allowed source."""
    out: set = set()
    pvm = raw.get("pricing_verified_models")
    if not isinstance(pvm, dict) or not pvm:
        return out
    max_age = _MAX_REGISTRY_AGE_HOURS
    reg_age = _parse_registry_age_hours(raw)
    if enforce_ttl and max_age is not None:
        if reg_age is not None and reg_age > float(max_age):
            return out
    now = time.time()
    for cid, meta in pvm.items():
        if not isinstance(cid, str) or not isinstance(meta, dict):
            continue
        if str(meta.get("prompt_usd")) != "0" or str(meta.get("completion_usd")) != "0":
            continue
        if meta.get("source") not in ("openrouter_catalog_zero", "kilo_catalog_zero"):
            continue
        if enforce_ttl and max_age is not None:
            ve_raw = meta.get("verified_epoch")
            if ve_raw is None:
                continue
            try:
                ve = float(ve_raw)
                if not math.isfinite(ve) or ve <= 0:
                    continue
                age_h = max(0.0, (now - ve) / 3600.0)
                if age_h > float(max_age):
                    continue
            except Exception:
                continue
        out.add(cid)
    return out


def _extract_curated_keyless_ids(raw: dict) -> set:
    if "curated_keyless_allowlist_models" not in raw:
        return set(KEYLESS_ALLOWLIST)
    curated = raw.get("curated_keyless_allowlist_models")
    out: set = set()
    if isinstance(curated, dict):
        for cid in curated.keys():
            if isinstance(cid, str) and cid in KEYLESS_ALLOWLIST:
                out.add(cid)
    return out & set(KEYLESS_ALLOWLIST)


def _validate_registry_structure(raw: dict, strict: bool = False) -> Tuple[bool, str]:
    if not isinstance(raw, dict):
        return False, "registry root must be a JSON object"
    tp = raw.get("task_pools") or raw.get("or_free_task_pools")
    if not isinstance(tp, dict):
        return False, "missing task_pools"
    id_re = re.compile(r"^[a-zA-Z0-9._:/-]+$")
    for tname in ("general", "code", "long", "fast"):
        if tname not in tp or not isinstance(tp[tname], dict):
            return False, f"missing task_pools.{tname}"
        for k in ("interleave_schedule", "fallback_chain"):
            lst = tp[tname].get(k)
            if not isinstance(lst, list):
                return False, f"task_pools.{tname}.{k} must be a list"
            # long may be empty; other pools must be non-empty in strict mode
            if tname != "long" and strict and not lst:
                return False, f"task_pools.{tname}.{k} is empty"
            for cid in lst:
                if not isinstance(cid, str) or len(cid) > 120 or not id_re.match(cid):
                    return False, f"invalid model id in {tname}.{k}: {cid!r}"
    pvm = raw.get("pricing_verified_models")
    if pvm is None:
        return False, "missing pricing_verified_models"
    if not isinstance(pvm, dict):
        return False, "pricing_verified_models must be an object"
    return True, "ok"


def _fail_registry(msg: str) -> None:
    sys.stderr.write(f"[REGISTRY ERROR] {msg}\n")
    sys.exit(4)


def _parse_and_validate_max_age_hours(val_raw: Any, source_label: str = "--max-registry-age-hours") -> float:
    try:
        val = float(val_raw)
    except Exception:
        _fail_registry(f"{source_label} must be a finite positive number (got {val_raw!r})")
    if not math.isfinite(val) or val <= 0:
        _fail_registry(f"{source_label} must be a finite positive number (got {val_raw!r})")
    return val


def _apply_registry_payload(raw: dict, rp: Path, file_bytes: bytes, strict: bool) -> None:
    global VERIFIED_ZERO_PRICE_IDS, CURATED_KEYLESS_IDS, _REGISTRY_VERIFIED_IDS
    global _REGISTRY_LOADED_FROM, _REGISTRY_UPDATED_AT, _REGISTRY_SHA256, _REGISTRY_AGE_HOURS
    global _REGISTRY_INITIALIZED

    ok, reason = _validate_registry_structure(raw, strict=strict)
    if not ok:
        if strict:
            _fail_registry(f"{rp}: {reason}")
        raise ValueError(reason)

    _REGISTRY_INITIALIZED = True
    _REGISTRY_LOADED_FROM = str(rp)
    _REGISTRY_UPDATED_AT = str(raw.get("updated_at") or "")
    _REGISTRY_SHA256 = _sha256_bytes(file_bytes)
    _REGISTRY_AGE_HOURS = _parse_registry_age_hours(raw)

    enforce_ttl = (_MAX_REGISTRY_AGE_HOURS is not None)
    if enforce_ttl and _MAX_REGISTRY_AGE_HOURS is not None:
        if _REGISTRY_AGE_HOURS is not None and _REGISTRY_AGE_HOURS > float(_MAX_REGISTRY_AGE_HOURS):
            if strict:
                _fail_registry(
                    f"{rp}: registry age {_REGISTRY_AGE_HOURS:.1f}h exceeds max {_MAX_REGISTRY_AGE_HOURS}h"
                )

    zero_ids = _extract_pricing_verified_ids(raw, enforce_ttl=enforce_ttl)
    curated = _extract_curated_keyless_ids(raw)
    if enforce_ttl and _MAX_REGISTRY_AGE_HOURS is not None:
        if _REGISTRY_AGE_HOURS is None or _REGISTRY_AGE_HOURS > float(_MAX_REGISTRY_AGE_HOURS):
            curated = set()

    if strict and not zero_ids and not curated:
        _fail_registry(f"{rp}: no pricing_verified_models and no curated keyless ids after policy/TTL check")

    # Populate verified sets BEFORE filtering pools (NEVER fall back to BUILTIN_CATALOG_ZERO_IDS here)
    VERIFIED_ZERO_PRICE_IDS = set(zero_ids)
    CURATED_KEYLESS_IDS = set(curated)
    _REGISTRY_VERIFIED_IDS = set(zero_ids) | set(curated)

    tp = raw.get("task_pools") or raw.get("or_free_task_pools") or {}
    for tname in ("general", "code", "long", "fast"):
        tval = tp[tname]
        safe_inter = []
        safe_fall = []
        for c in list(tval.get("interleave_schedule") or []):
            platform, model_id, _ = normalize_candidate_spec(c)
            if is_safe_free_target(platform, model_id):
                safe_inter.append(c)
            elif strict:
                _fail_registry(f"{rp}: task_pools.{tname} contains unverified id {c}")
        for c in list(tval.get("fallback_chain") or []):
            platform, model_id, _ = normalize_candidate_spec(c)
            if is_safe_free_target(platform, model_id):
                safe_fall.append(c)
            elif strict:
                _fail_registry(f"{rp}: task_pools.{tname} contains unverified id {c}")
        if tname != "long" and not safe_fall and strict:
            _fail_registry(f"{rp}: task_pools.{tname} empty after safety filter (no static fallback in strict mode)")
        # Always overwrite TASK_POOLS[tname] (never keep stale module-level static pools when a registry is loaded)
        TASK_POOLS[tname]["description"] = str(tval.get("description", TASK_POOLS[tname]["description"]))
        TASK_POOLS[tname]["interleave_schedule"] = safe_inter
        TASK_POOLS[tname]["fallback_chain"] = safe_fall

    if _REGISTRY_AGE_HOURS is not None and _REGISTRY_AGE_HOURS > MAX_REGISTRY_AGE_HOURS_DEFAULT_WARN:
        sys.stderr.write(
            f"[REGISTRY STALE WARNING] Registry is >7 days old ({_REGISTRY_AGE_HOURS:.1f}h); "
            f"run scan_and_sync_free_models.py --apply\n"
        )


def _load_dynamic_registry() -> None:
    global VERIFIED_ZERO_PRICE_IDS, CURATED_KEYLESS_IDS, _REGISTRY_VERIFIED_IDS
    global _REGISTRY_LOADED_FROM, _REGISTRY_UPDATED_AT, _REGISTRY_SHA256, _REGISTRY_AGE_HOURS
    global _MAX_REGISTRY_AGE_HOURS, _REGISTRY_INITIALIZED

    if _MAX_REGISTRY_AGE_HOURS is None:
        env_max = (os.environ.get("OR_FREE_MAX_REGISTRY_AGE_HOURS") or "").strip()
        if env_max:
            _MAX_REGISTRY_AGE_HOURS = _parse_and_validate_max_age_hours(env_max, "OR_FREE_MAX_REGISTRY_AGE_HOURS")

    # CLI --registry (_EXPLICIT_REGISTRY_PATH) takes strict precedence over OR_FREE_REGISTRY_PATH
    explicit = _EXPLICIT_REGISTRY_PATH
    env_reg = "" if explicit else (os.environ.get("OR_FREE_REGISTRY_PATH") or "").strip()
    strict = bool(_STRICT_REGISTRY_MODE or explicit or env_reg)

    if strict:
        path_s = explicit or env_reg
        rp = Path(path_s).expanduser()
        if not rp.is_file():
            _fail_registry(f"strict registry path not found: {rp}")
        try:
            file_bytes = rp.read_bytes()
            raw = json.loads(file_bytes.decode("utf-8"))
        except Exception as e:
            _fail_registry(f"strict registry unreadable/invalid JSON: {e}")
        _apply_registry_payload(raw, rp, file_bytes, strict=True)
        return

    for rp in registry_candidate_paths():
        try:
            if not rp.is_file():
                continue
            file_bytes = rp.read_bytes()
            raw = json.loads(file_bytes.decode("utf-8"))
            _apply_registry_payload(raw, rp, file_bytes, strict=False)
            return
        except SystemExit:
            raise
        except Exception:
            continue

    # No registry file found on disk: builtin seed ONLY when no hard TTL is active
    _REGISTRY_INITIALIZED = True
    _REGISTRY_LOADED_FROM = "(static-builtin)"
    _REGISTRY_UPDATED_AT = None
    _REGISTRY_SHA256 = None
    _REGISTRY_AGE_HOURS = None
    if _MAX_REGISTRY_AGE_HOURS is not None:
        # Timeless builtin seed lacks verified_epoch and cannot bypass hard TTL
        VERIFIED_ZERO_PRICE_IDS = set()
        CURATED_KEYLESS_IDS = set()
        _REGISTRY_VERIFIED_IDS = set()
        for tname in ("general", "code", "long", "fast"):
            TASK_POOLS[tname]["interleave_schedule"] = []
            TASK_POOLS[tname]["fallback_chain"] = []
    else:
        VERIFIED_ZERO_PRICE_IDS = set(BUILTIN_CATALOG_ZERO_IDS)
        CURATED_KEYLESS_IDS = set(KEYLESS_ALLOWLIST)
        _REGISTRY_VERIFIED_IDS = set(BUILTIN_CATALOG_ZERO_IDS) | set(KEYLESS_ALLOWLIST)


def validate_registry_cli(path: str) -> None:
    """Strict offline validation; print JSON and exit 0/4."""
    global _STRICT_REGISTRY_MODE, _EXPLICIT_REGISTRY_PATH
    _STRICT_REGISTRY_MODE = True
    _EXPLICIT_REGISTRY_PATH = path
    # Reload
    _load_dynamic_registry()
    rp = Path(path).expanduser().resolve()
    result = {
        "valid": True,
        "registry_path": str(rp),
        "sha256": _REGISTRY_SHA256,
        "pools": {t: list(TASK_POOLS[t]["fallback_chain"]) for t in TASK_POOLS},
        "verified_zero_count": len(VERIFIED_ZERO_PRICE_IDS),
        "curated_keyless_count": len(CURATED_KEYLESS_IDS),
        "updated_at": _REGISTRY_UPDATED_AT,
        "age_hours": _REGISTRY_AGE_HOURS,
    }
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0)


# Sensitive data patterns for local DLP interception
SECRET_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("PEM/SSH Private Key", re.compile(r"-----BEGIN\s+[A-Z0-9\s]*PRIVATE\s+KEY-----", re.IGNORECASE)),
    ("OpenRouter API Key", re.compile(r"sk-or-v1-[a-f0-9]{32,}", re.IGNORECASE)),
    ("Generic sk- API Key", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b")),
    ("NVIDIA NIM API Key", re.compile(r"\bnvapi-[A-Za-z0-9_\-]{20,}\b")),
    ("OpenAI/Anthropic Secret Key", re.compile(r"sk-(?:proj-|ant-api03-)[A-Za-z0-9_\-]{20,}")),
    ("AWS Access Key ID", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("GitHub PAT Token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b")),
    ("Cloudflare API Token", re.compile(r"\bcfut_[A-Za-z0-9_\-]{30,}\b")),
    ("Bearer Token", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{20,}\b")),
    ("Password Assignment", re.compile(r"(?i)\b(?:password|passwd|pwd)\s*[:=]\s*\S{6,}")),
    ("Explicit Secret Marker", re.compile(r"(?:\[SECRET\]|\[CONFIDENTIAL\]|\[涉密\]|【涉密】|绝密数据)", re.IGNORECASE)),
]


def get_state_path() -> Path:
    return get_config_dir() / "router_state.json"


def get_logs_dir() -> Path:
    return get_config_dir() / "logs"


def now_utc8() -> datetime:
    return datetime.now(TZ_UTC8)


def today_utc8_str() -> str:
    return now_utc8().strftime("%Y-%m-%d")


def detect_caller(explicit_caller: Optional[str] = None) -> str:
    if explicit_caller and explicit_caller.strip():
        return explicit_caller.strip()[:48]
    for env_k in ("OR_FREE_CALLER", "BOT_NAME", "AGENT_NAME", "CURSOR_AGENT_NAME"):
        val = os.environ.get(env_k, "").strip()
        if val:
            return val[:48]
    host = socket.gethostname()[:16]
    cwd_name = Path.cwd().name or "root"
    ppid_comm = ""
    if os.name != "nt":
        try:
            comm_p = Path(f"/proc/{os.getppid()}/comm")
            if comm_p.is_file():
                ppid_comm = comm_p.read_text(encoding="utf-8", errors="ignore").strip()
        except Exception:
            pass
    if ppid_comm:
        return f"{host}:{ppid_comm}@{cwd_name}"[:48]
    return f"{host}@{cwd_name}"[:48]


def sanitize_preview(text: str, max_chars: int = 60) -> str:
    if not text:
        return ""
    clean = " ".join(text.replace("\r", " ").replace("\n", " ").split())
    for label, pat in SECRET_PATTERNS:
        clean = pat.sub(f"[REDACTED:{label}]", clean)
    if len(clean) > max_chars:
        return clean[:max_chars] + "…"
    return clean


def rotate_and_prune_logs(retention_days: int = LOG_RETENTION_DAYS) -> Dict[str, List[str]]:
    """
    Zero-dependency log maintenance:
      1. Compress any `usage-YYYY-MM-DD.jsonl` from previous days (`date < today_utc8`)
         into `usage-YYYY-MM-DD.zip` (ZIP_DEFLATED) and delete the raw `.jsonl`.
      2. Delete any `usage-YYYY-MM-DD.zip` (or `.jsonl`/`.gz`) older than `retention_days` (default 30 days).
    """
    log_dir = get_logs_dir()
    zipped: List[str] = []
    deleted: List[str] = []
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        return {"zipped": zipped, "deleted": deleted}

    today_dt = now_utc8().date()

    for entry in sorted(log_dir.iterdir()):
        if not entry.is_file() or entry.is_symlink():
            continue
        m = LOG_DATE_RE.match(entry.name)
        if not m:
            continue
        date_str, ext = m.group(1), m.group(2)
        try:
            file_dt = date.fromisoformat(date_str)
        except ValueError:
            continue

        age_days = (today_dt - file_dt).days

        # 1. If older than retention_days (e.g. > 30 days), delete immediately
        if age_days > retention_days:
            try:
                entry.unlink()
                deleted.append(entry.name)
            except Exception:
                pass
            continue

        # 2. If it's a previous day's raw `.jsonl` (age_days >= 1), zip it and remove `.jsonl`
        if ext == "jsonl" and age_days >= 1:
            zip_path = log_dir / f"usage-{date_str}.zip"
            try:
                with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
                    zf.write(entry, arcname=entry.name)
                # Verify zip integrity before unlinking raw jsonl
                with zipfile.ZipFile(zip_path, mode="r") as zf_check:
                    bad = zf_check.testzip()
                    names = zf_check.namelist()
                if bad is None and entry.name in names and zip_path.stat().st_size > 0:
                    entry.unlink()
                    zipped.append(zip_path.name)
            except Exception:
                pass

    return {"zipped": zipped, "deleted": deleted}


def append_audit_log(
    caller: str,
    task: str,
    route: str,
    status: Any,
    elapsed_ms: float,
    tokens_in: int,
    tokens_out: int,
    prompt_text: str,
    reply_text: str,
    switched_from: Optional[str] = None,
) -> None:
    """
    Append a single compact JSONL record (~250-350 bytes) to today's 24h log file
    and trigger automatic zip rotation / 30-day pruning.
    """
    try:
        rotate_and_prune_logs()
        log_dir = get_logs_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        day_str = today_utc8_str()
        log_file = log_dir / f"usage-{day_str}.jsonl"

        # Default: metadata only. Previews require OR_FREE_AUDIT_PREVIEW=1.
        record: Dict[str, Any] = {
            "ts": now_utc8().strftime("%Y-%m-%d %H:%M:%S+08:00"),
            "caller": caller,
            "task": task,
            "route": route,
            "status": status,
            "ms": round(elapsed_ms, 1),
            "tokens": f"{tokens_in}/{tokens_out}",
        }
        if os.environ.get("OR_FREE_AUDIT_PREVIEW", "").strip() in ("1", "true", "yes"):
            # Never preview DLP-blocked prompts
            st = str(status)
            if not st.startswith("BLOCKED_DLP"):
                record["prompt_preview"] = sanitize_preview(prompt_text, 60)
                record["reply_preview"] = sanitize_preview(reply_text, 60)
        if switched_from:
            record["switched_from"] = switched_from[:80]

        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(line)

        # Update convenience symlink `current.jsonl` on POSIX
        if os.name != "nt":
            cur_link = log_dir / "current.jsonl"
            try:
                if cur_link.is_symlink() or cur_link.exists():
                    cur_link.unlink()
                cur_link.symlink_to(log_file.name)
            except Exception:
                pass
    except Exception:
        pass


def show_audit_logs(limit: int = 20, as_json: bool = False) -> None:
    maint = rotate_and_prune_logs()
    log_dir = get_logs_dir()
    log_dir.mkdir(parents=True, exist_ok=True)

    active_files: List[Dict[str, Any]] = []
    zip_archives: List[Dict[str, Any]] = []
    total_bytes = 0

    for entry in sorted(log_dir.iterdir()):
        if not entry.is_file() or entry.is_symlink():
            continue
        sz = entry.stat().st_size
        total_bytes += sz
        if entry.name.endswith(".jsonl"):
            lines_cnt = 0
            try:
                with open(entry, "r", encoding="utf-8", errors="replace") as f:
                    lines_cnt = sum(1 for _ in f)
            except Exception:
                pass
            active_files.append({"name": entry.name, "bytes": sz, "entries": lines_cnt})
        elif entry.name.endswith(".zip"):
            zip_archives.append({"name": entry.name, "bytes": sz})

    # Collect up to `limit` most recent records across today's jsonl (and newest zip if needed)
    records: List[Dict[str, Any]] = []
    day_str = today_utc8_str()
    today_file = log_dir / f"usage-{day_str}.jsonl"
    if today_file.is_file():
        try:
            for raw_line in today_file.read_text(encoding="utf-8", errors="replace").splitlines():
                if raw_line.strip():
                    records.append(json.loads(raw_line))
        except Exception:
            pass

    if len(records) < limit and zip_archives:
        for zinfo in reversed(zip_archives):
            if len(records) >= limit:
                break
            zp = log_dir / zinfo["name"]
            try:
                with zipfile.ZipFile(zp, "r") as zf:
                    for member in zf.namelist():
                        raw_bytes = zf.read(member)
                        older_recs = [
                            json.loads(line)
                            for line in raw_bytes.decode("utf-8", errors="replace").splitlines()
                            if line.strip()
                        ]
                        records = older_recs + records
            except Exception:
                pass

    recent = records[-limit:] if limit > 0 else records

    if as_json:
        print(json.dumps({
            "log_dir": str(log_dir),
            "retention_policy": f"Active 24h .jsonl -> next-day .zip (ZIP_DEFLATED) -> auto-delete > {LOG_RETENTION_DAYS} days",
            "maintenance_just_ran": maint,
            "total_storage_bytes": total_bytes,
            "active_jsonl_files": active_files,
            "zip_archives": zip_archives,
            "recent_count": len(recent),
            "recent_entries": recent,
        }, indent=2, ensure_ascii=False))
        return

    print("=== OR-Free 24h Audit Log & Auto-Zip Archive Status ===")
    print(f"  Log Directory    : {log_dir}")
    print(f"  Retention Policy : Record 24h (.jsonl) -> Next-Day Auto-.zip -> Auto-Delete after {LOG_RETENTION_DAYS} days")
    print(f"  Total Disk Usage : {total_bytes / 1024.0:.2f} KB across {len(active_files)} active + {len(zip_archives)} .zip archive(s)")
    if maint["zipped"] or maint["deleted"]:
        print(f"  Auto-Maintenance : zipped={maint['zipped']} | deleted_expired={maint['deleted']}")
    if active_files:
        for af in active_files:
            print(f"  Active 24h Log   : {af['name']} ({af['entries']} calls, {af['bytes']} bytes)")
    if zip_archives:
        z_summary = ", ".join(f"{z['name']} ({z['bytes']} B)" for z in zip_archives[-5:])
        print(f"  Zip Archives     : {z_summary}")

    print(f"\n--- Last {len(recent)} Call Records (most recent last) ---")
    if not recent:
        print("  (No log entries yet)")
        return
    for idx, r in enumerate(recent, 1):
        sw = f" [switched_from: {r['switched_from']}]" if r.get("switched_from") else ""
        print(
            f"  {idx:2d}. [{r.get('ts')}] caller={r.get('caller')} | task={r.get('task')} | "
            f"status={r.get('status')} | {r.get('ms')}ms | tok={r.get('tokens')} | route={r.get('route')}{sw}"
        )
        print(f"      prompt: {r.get('prompt_preview')!r} -> reply: {r.get('reply_preview')!r}")


PROVIDER_KEY_CONFIG: Dict[str, Dict[str, Tuple[str, ...]]] = {
    "openrouter": {
        "env_names": ("OPENROUTER_API_KEY", "OR_API_KEY"),
        "file_names": ("openrouter_api_key", "api_key"),
    },
    "nvidia": {
        "env_names": ("NVIDIA_API_KEY",),
        "file_names": ("nvidia_api_key",),
    },
    "routeopen": {
        "env_names": ("ROUTEOPEN_API_KEY",),
        "file_names": ("routeopen_api_key",),
    },
}


def _extract_first_key_line(raw: str) -> str:
    for line in (raw or "").splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            return s
    return ""


def _parse_keys_env_file(path: Path, env_names: Tuple[str, ...]) -> str:
    try:
        if not path.is_file():
            return ""
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return ""
    parsed: Dict[str, str] = {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip()
        if len(v) >= 2 and ((v[0] == v[-1] == '"') or (v[0] == v[-1] == "'")):
            v = v[1:-1].strip()
        if k and v and k not in parsed:
            parsed[k] = v
    for name in env_names:
        if parsed.get(name):
            return parsed[name]
    return ""


def get_key_paths() -> List[Path]:
    """OpenRouter key file candidates in resolution priority order."""
    dirs = [
        get_app_home(),
        Path.home() / ".config" / "openrouter",
        Path.home() / ".ob1",
    ]
    paths: List[Path] = []
    for d in dirs:
        paths.append(d / "openrouter_api_key")
        paths.append(d / "api_key")
    for d in dirs:
        paths.append(d / "keys.env")
    return paths


def mask_key(key: str) -> str:
    """Mask secrets in status output (do not leak long prefixes)."""
    key = key.strip()
    if len(key) <= 8:
        return "***"
    return f"***{key[-4:]}"


def load_provider_key(platform: str) -> str:
    """Unified key resolution across router and scanner: env -> plain key files -> keys.env."""
    cfg = PROVIDER_KEY_CONFIG.get(platform)
    if not cfg:
        pcfg = PROVIDERS.get(platform, {})
        if pcfg.get("keyless", True):
            return ""
        env_var = pcfg.get("key_env", "")
        key_file = pcfg.get("key_file", "")
        env_names = (env_var,) if env_var else ()
        file_names = (key_file,) if key_file else ()
    else:
        env_names = cfg["env_names"]
        file_names = cfg["file_names"]

    for env_var in env_names:
        val = _extract_first_key_line(os.environ.get(env_var, ""))
        if val:
            return val

    search_dirs = [
        get_app_home(),
        Path.home() / ".config" / "openrouter",
        Path.home() / ".ob1",
    ]
    for d in search_dirs:
        for fname in file_names:
            p = d / fname
            try:
                if p.is_file():
                    val = _extract_first_key_line(p.read_text(encoding="utf-8", errors="ignore"))
                    if val:
                        return val
            except Exception:
                continue

    for d in search_dirs:
        val = _parse_keys_env_file(d / "keys.env", env_names)
        if val:
            return val

    return ""


def load_api_key() -> str:
    """Resolve OpenRouter key using unified provider key resolution."""
    return load_provider_key("openrouter")


def check_sensitive_content(text: str) -> Optional[str]:
    """Scan prompt text for private keys, credentials, or explicit confidential tags."""
    for label, pattern in SECRET_PATTERNS:
        if pattern.search(text):
            return label
    return None


# ── Model Cost Safety Guard ───────────────────────────────────────────────────

def normalize_candidate_spec(spec: str) -> Tuple[str, str, str]:
    spec = spec.strip()
    if spec == "openrouter/free":
        return ("openrouter", "openrouter/free", "openrouter/openrouter/free")
    first_slash = spec.find("/")
    if first_slash > 0:
        prefix = spec[:first_slash].lower()
        rest = spec[first_slash + 1:]
        if prefix in PROVIDERS:
            return (prefix, rest, f"{prefix}/{rest}")
    return ("openrouter", spec, f"openrouter/{spec}")


def is_safe_free_target(
    platform: str,
    model_id: str,
    allow_unverified: bool = False,
    registry_ids: Optional[set] = None,
) -> bool:
    """Strict zero-billing gate. Unknown/unverified models are denied by default.

    openrouter/kilo: require :free (or known free aliases) AND membership in
    VERIFIED_ZERO_PRICE_IDS (from pricing_verified_models or BUILTIN_CATALOG_ZERO_IDS).
    ovh/llm7: require KEYLESS_ALLOWLIST / CURATED_KEYLESS_IDS membership.
    nvidia/routeopen: always False unless --allow-unverified-model (caller warned).
    """
    platform = (platform or "").strip().lower()
    model_id = (model_id or "").strip()
    mid = model_id.lower()
    canonical = f"{platform}/{model_id}"
    allow_unverified = allow_unverified or _ALLOW_UNVERIFIED_MODEL

    if not _REGISTRY_INITIALIZED:
        _load_dynamic_registry()

    zero_ids = registry_ids if registry_ids is not None else VERIFIED_ZERO_PRICE_IDS
    keyless = CURATED_KEYLESS_IDS

    if platform == "openrouter":
        if not (mid.endswith(":free") or mid == "openrouter/free"):
            return False
        return canonical in zero_ids

    if platform == "kilo":
        if not (mid.endswith(":free") or mid in ("reflection/beam-free", "kilo-auto/free", "openrouter/free")):
            return False
        return canonical in zero_ids

    if platform in ("ovh", "llm7"):
        if registry_ids is not None:
            return canonical in registry_ids or canonical in keyless
        return canonical in keyless

    if platform in ("nvidia", "routeopen"):
        # Only reachable via explicit --allow-unverified-model (zero cost NOT guaranteed).
        return bool(allow_unverified)

    # Custom/unknown platforms: only with explicit override
    return bool(allow_unverified)

def validate_model_safety(
    spec: str,
    allow_paid: bool = False,  # retained for CLI compat; ALWAYS ignored (hard lock)
    caller: str = "",
    task: str = "auto",
    prompt_text: str = "",
) -> Tuple[str, str, str]:
    _ = allow_paid  # hard-locked: paid models are never allowed
    platform, model_id, canonical_id = normalize_candidate_spec(spec)
    if not is_safe_free_target(platform, model_id):
        append_audit_log(
            caller=caller or detect_caller(),
            task=task,
            route=canonical_id,
            status="BLOCKED_PAID",
            elapsed_ms=0.0,
            tokens_in=0,
            tokens_out=0,
            prompt_text="",  # never store blocked prompt body by default
            reply_text=f"Blocked non-free/unverified model {spec}",
        )
        sys.stderr.write(
            f"[SAFETY BLOCK] Model '{spec}' is NOT in pricing_verified_models / curated keyless allowlist!\n"
            f"Refusing to call (exit 2). OpenRouter/Kilo require live catalog $0 proof in the registry;\n"
            f"OVH/LLM7 require curated keyless allowlist membership. Use --allow-unverified-model only as last resort.\n"
        )
        sys.exit(2)
    return platform, model_id, canonical_id


# ── Proactive Watermark & State Management ────────────────────────────────────

def current_day_utc() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def load_router_state() -> Dict[str, Any]:
    path = get_state_path()
    today = current_day_utc()
    default_state: Dict[str, Any] = {
        "version": 2,
        "day_utc": today,
        "rr_counters": {"general": 0, "code": 0, "long": 0, "fast": 0},
        "provider_rpm_timestamps": {},
        "provider_rpd_counts": {},
        "model_rpm_timestamps": {},
        "model_rpd_counts": {},
        "cooldowns": {},
        "stats": {},
        "or_remote_daily_used": None,
    }
    try:
        if path.is_file():
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                if raw.get("day_utc") != today:
                    raw["day_utc"] = today
                    raw["provider_rpd_counts"] = {}
                    raw["model_rpd_counts"] = {}
                    raw["or_remote_daily_used"] = None
                for k, v in default_state.items():
                    raw.setdefault(k, v)
                return raw
    except Exception:
        pass
    return default_state


def save_router_state(state: Dict[str, Any], strict: bool = True) -> None:
    path = get_state_path()
    tmp: Optional[Path] = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except Exception:
            pass
        os.replace(str(tmp), str(path))
    except Exception as e:
        if tmp is not None:
            try:
                if tmp.exists():
                    tmp.unlink()
            except Exception:
                pass
        if strict:
            raise RuntimeError(f"Failed to persist router state to {path}: {e}") from e


def _state_lock_path() -> Path:
    return get_app_home() / "router_state.lock"


class RouterStateLock:
    """Cross-process and cross-thread exclusive lock around load -> reserve budget -> save."""

    def __init__(self, timeout_sec: Optional[float] = None):
        self._fh = None
        self._inproc_acquired = False
        if timeout_sec is not None:
            self._timeout_sec = float(timeout_sec)
        else:
            try:
                self._timeout_sec = float(os.environ.get("OR_FREE_LOCK_TIMEOUT_SEC", "5.0"))
            except Exception:
                self._timeout_sec = 5.0

    def __enter__(self):
        if not _INPROC_STATE_LOCK.acquire(timeout=max(0.05, self._timeout_sec)):
            raise RuntimeError("Timed out acquiring in-process router state lock")
        self._inproc_acquired = True
        path = _state_lock_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = open(path, "a+", encoding="utf-8")
        except Exception as e:
            _INPROC_STATE_LOCK.release()
            self._inproc_acquired = False
            raise RuntimeError(f"Failed to open router state lock {path}: {e}") from e

        deadline = time.monotonic() + max(0.0, self._timeout_sec)
        last_err: Optional[Exception] = None
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    self._fh.seek(0)
                    self._fh.write("0")
                    self._fh.flush()
                    self._fh.seek(0)
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except Exception as e:
                last_err = e
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.02)

        try:
            self._fh.close()
        except Exception:
            pass
        self._fh = None
        if self._inproc_acquired:
            _INPROC_STATE_LOCK.release()
            self._inproc_acquired = False
        raise RuntimeError(f"Failed to acquire exclusive lock on {path}: {last_err}") from last_err

    def __exit__(self, exc_type, exc, tb):
        try:
            if self._fh is not None:
                if os.name == "nt":
                    import msvcrt
                    self._fh.seek(0)
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            if self._fh is not None:
                self._fh.close()
        except Exception:
            pass
        self._fh = None
        if self._inproc_acquired:
            try:
                _INPROC_STATE_LOCK.release()
            except Exception:
                pass
            self._inproc_acquired = False
        return False


def reserve_candidate_budget(
    canonical_id: str,
    state: Dict[str, Any],
    now: float,
) -> None:
    """Reserve RPM/RPD timestamp slot BEFORE the network request (under lock)."""
    platform, _, _ = normalize_candidate_spec(canonical_id)
    p_ts = prune_60s(state.setdefault("provider_rpm_timestamps", {}).get(platform, []), now)
    p_ts.append(now)
    state["provider_rpm_timestamps"][platform] = p_ts
    m_ts = prune_60s(state.setdefault("model_rpm_timestamps", {}).get(canonical_id, []), now)
    m_ts.append(now)
    state["model_rpm_timestamps"][canonical_id] = m_ts
    state.setdefault("provider_rpd_counts", {})[platform] = (
        state.get("provider_rpd_counts", {}).get(platform, 0) + 1
    )
    state.setdefault("model_rpd_counts", {})[canonical_id] = (
        state.get("model_rpd_counts", {}).get(canonical_id, 0) + 1
    )


def prune_60s(timestamps: List[float], now: float) -> List[float]:
    cutoff = now - 60.0
    return [t for t in timestamps if t > cutoff]


def check_candidate_gate(
    canonical_id: str,
    state: Dict[str, Any],
    now: float,
    rpm_watermark_override: Optional[int] = None,
    model_rpd_watermark_override: Optional[int] = None,
    has_key_override: bool = False,
) -> Optional[str]:
    platform, model_id, _ = normalize_candidate_spec(canonical_id)
    p_cfg = PROVIDERS.get(platform, PROVIDERS["openrouter"])

    # 0. Check if keyed provider has its key configured
    if not p_cfg.get("keyless", True) and not (has_key_override or load_provider_key(platform)):
        return f"waiting_for_key ({p_cfg.get('key_env', 'API_KEY')} not configured)"

    # 1. Check active cooldown (from prior 429 / 5xx / timeout)
    cd = state.get("cooldowns", {}).get(canonical_id)
    if cd and now < cd.get("until", 0):
        rem = int(cd["until"] - now)
        return f"cooldown ({cd.get('reason', 'error')}, {rem}s left)"

    # 2. Check Provider 60s RPM Watermark (Proactive switching BEFORE 429!)
    p_ts = prune_60s(state.get("provider_rpm_timestamps", {}).get(platform, []), now)
    state.setdefault("provider_rpm_timestamps", {})[platform] = p_ts
    rpm_wm = rpm_watermark_override if rpm_watermark_override is not None else p_cfg["rpm_watermark"]
    if len(p_ts) >= rpm_wm:
        return f"proactive_provider_rpm_watermark ({len(p_ts)}/{rpm_wm} req/60s on {platform})"

    # 3. Check Provider Daily RPD Watermark
    p_rpd = state.get("provider_rpd_counts", {}).get(platform, 0)
    if platform == "openrouter" and state.get("or_remote_daily_used") is not None:
        p_rpd = max(p_rpd, int(state["or_remote_daily_used"]))
    if p_rpd >= p_cfg["rpd_watermark"]:
        return f"proactive_provider_rpd_watermark ({p_rpd}/{p_cfg['rpd_watermark']} req/day on {platform})"

    # 4. Check Single-Model Daily RPD Watermark (80% of 50 = 40 RPD on OpenRouter)
    m_rpd = state.get("model_rpd_counts", {}).get(canonical_id, 0)
    m_rpd_wm = model_rpd_watermark_override if model_rpd_watermark_override is not None else p_cfg["model_rpd_watermark"]
    if m_rpd >= m_rpd_wm:
        return f"proactive_model_rpd_watermark ({m_rpd}/{m_rpd_wm} req/day on model)"

    # 5. Check Single-Model RPM watermark (uses full provider RPM watermark so the #1 strongest model stays active until quota is reached)
    m_ts = prune_60s(state.get("model_rpm_timestamps", {}).get(canonical_id, []), now)
    state.setdefault("model_rpm_timestamps", {})[canonical_id] = m_ts
    model_rpm_wm = rpm_wm
    if len(m_ts) >= model_rpm_wm:
        return f"proactive_model_rpm_watermark ({len(m_ts)}/{model_rpm_wm} req/60s on model)"

    return None


def record_attempt_result(
    canonical_id: str,
    state: Dict[str, Any],
    now: float,
    success: bool,
    status_code: int,
    elapsed_ms: float,
    err_msg: str = "",
) -> None:
    platform, _, _ = normalize_candidate_spec(canonical_id)

    p_ts = prune_60s(state.setdefault("provider_rpm_timestamps", {}).get(platform, []), now)
    p_ts.append(now)
    state["provider_rpm_timestamps"][platform] = p_ts

    m_ts = prune_60s(state.setdefault("model_rpm_timestamps", {}).get(canonical_id, []), now)
    m_ts.append(now)
    state["model_rpm_timestamps"][canonical_id] = m_ts

    state.setdefault("provider_rpd_counts", {})[platform] = state.get("provider_rpd_counts", {}).get(platform, 0) + 1
    state.setdefault("model_rpd_counts", {})[canonical_id] = state.get("model_rpd_counts", {}).get(canonical_id, 0) + 1

    st = state.setdefault("stats", {}).setdefault(
        canonical_id, {"succ": 0, "fail": 0, "avg_ms": 0.0, "last_status": 0}
    )
    st["last_status"] = status_code

    if success:
        st["succ"] = st.get("succ", 0) + 1
        prev_avg = st.get("avg_ms", 0.0)
        st["avg_ms"] = round(elapsed_ms if prev_avg <= 0 else (0.3 * elapsed_ms + 0.7 * prev_avg), 1)
        if canonical_id in state.get("cooldowns", {}):
            del state["cooldowns"][canonical_id]
    else:
        st["fail"] = st.get("fail", 0) + 1
        cds = state.setdefault("cooldowns", {})
        prev_cd = cds.get(canonical_id, {})
        level = min(prev_cd.get("level", 0) + 1, 3)
        if status_code == 429:
            dur = [180, 600, 1800, 3600][level - 1]
            reason = "upstream_429"
        elif status_code in (401, 402, 403, 404):
            dur = 3600
            reason = f"http_{status_code}"
        else:
            dur = 90
            reason = f"error_{status_code or 'timeout'}"
        cds[canonical_id] = {"until": now + dur, "reason": reason, "level": level, "set_at": now}


# ── Task Auto-Detection & Interleaved Candidate Ordering ──────────────────────

def finalize_attempt_result(
    canonical_id: str,
    state: Dict[str, Any],
    now: float,
    success: bool,
    status_code: int,
    elapsed_ms: float,
    err_msg: str = "",
) -> None:
    """Update stats/cooldowns AFTER a request. Does NOT re-increment RPM/RPD (already reserved)."""
    st = state.setdefault("stats", {}).setdefault(
        canonical_id, {"succ": 0, "fail": 0, "avg_ms": 0.0, "last_status": 0}
    )
    st["last_status"] = status_code
    if success:
        st["succ"] = st.get("succ", 0) + 1
        prev_avg = st.get("avg_ms", 0.0)
        st["avg_ms"] = round(elapsed_ms if prev_avg <= 0 else (0.3 * elapsed_ms + 0.7 * prev_avg), 1)
        if canonical_id in state.get("cooldowns", {}):
            del state["cooldowns"][canonical_id]
    else:
        st["fail"] = st.get("fail", 0) + 1
        cool_s = 30
        if status_code == 429:
            cool_s = 60
        elif status_code >= 500:
            cool_s = 45
        elif status_code == 0:
            cool_s = 20
        state.setdefault("cooldowns", {})[canonical_id] = {
            "until": now + cool_s,
            "reason": f"HTTP {status_code}" if status_code else (err_msg[:80] or "error"),
        }


def detect_task_type(prompt: str, requested_task: str) -> str:
    if requested_task and requested_task != "auto" and requested_task in TASK_POOLS:
        return requested_task
    if len(prompt) >= 15000:
        return "long"
    code_regex = re.compile(
        r"(?:\bdef\s+\w+|\bfunction\s+\w+|\bclass\s+\w+|```|\bimport\s+\w+|\bpublic\s+static\b|\bconsole\.log\b|traceback|syntaxerror|代码|函数|报错|bug|refactor)",
        re.IGNORECASE,
    )
    if code_regex.search(prompt):
        return "code"
    if len(prompt) <= 280:
        return "fast"
    return "general"


def build_ordered_candidates(
    task: str,
    state: Dict[str, Any],
    explicit_model: Optional[str] = None,
    prefer_provider: Optional[str] = None,
    strategy: str = "priority",
) -> Tuple[List[str], int]:
    if not _REGISTRY_INITIALIZED:
        _load_dynamic_registry()
    pool = TASK_POOLS.get(task, TASK_POOLS["general"])
    schedule = list(pool["interleave_schedule"])
    chain = list(pool["fallback_chain"])

    if strategy == "interleave":
        rr_map = state.setdefault("rr_counters", {})
        rr_idx = int(rr_map.get(task, 0))
        slot = rr_idx % len(schedule) if schedule else 0
        rr_map[task] = rr_idx + 1
        rotated_schedule = schedule[slot:] + schedule[:slot]
    else:
        # Strict Priority Cascade: always start at #1 strongest model; step down only when quota watermark or cooldown hits
        slot = 0
        rotated_schedule = schedule

    ordered: List[str] = []
    if explicit_model:
        _, _, can_id = normalize_candidate_spec(explicit_model)
        ordered.append(can_id)

    for item in rotated_schedule + chain:
        _, _, can_id = normalize_candidate_spec(item)
        if can_id not in ordered:
            ordered.append(can_id)

    if prefer_provider and prefer_provider in PROVIDERS:
        pref = [c for c in ordered if c.startswith(f"{prefer_provider}/")]
        rest = [c for c in ordered if not c.startswith(f"{prefer_provider}/")]
        ordered = pref + rest

    return ordered, slot



# ── Gate 1: HTTPS host whitelist + no-redirect opener ─────────────────────────

class _NoRedirect(urllib.request.HTTPErrorProcessor):
    """Refuse automatic redirects (Gate 1 hardening). Delegate non-3xx to parent so 4xx/5xx raise."""

    def http_response(self, request, response):  # type: ignore[no-untyped-def]
        code = response.getcode()
        if 300 <= code < 400:
            raise urllib.error.HTTPError(
                request.full_url, code, "Redirect blocked by Gate 1",
                response.headers, response,
            )
        return super().http_response(request, response)

    https_response = http_response


def _verify_outbound_url(url: str) -> None:
    """Reject non-HTTPS or non-whitelisted hosts before any network I/O."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme.lower() != "https":
        raise RuntimeError(f"Gate1: refusing non-HTTPS URL scheme={parsed.scheme!r}")
    host = (parsed.hostname or "").lower()
    if host not in WHITELISTED_DOMAINS:
        # Allow exact whitelist only (no suffix matching to avoid evil.openrouter.ai style tricks
        # unless host equals a listed domain).
        raise RuntimeError(f"Gate1: refusing non-whitelisted host {host!r}")


def _urlopen_no_redirect(req: urllib.request.Request, timeout: int = 20):
    """urlopen that never follows redirects; 3xx blocked; 4xx/5xx always raise HTTPError."""
    _verify_outbound_url(req.full_url)
    opener = urllib.request.build_opener(_NoRedirect)
    resp = opener.open(req, timeout=timeout)
    code = getattr(resp, "status", None) or resp.getcode()
    if int(code) >= 400:
        body = resp.read() if hasattr(resp, "read") else None
        raise urllib.error.HTTPError(
            req.full_url, int(code), f"HTTP {code}",
            getattr(resp, "headers", None), resp,
        )
    return resp


# ── HTTP / Subcommands (`--quota`, `--list`, `--routes`) ──────────────────────

def http_get_json(url: str, api_key: str = "", timeout: int = 20) -> Tuple[int, float, Dict[str, Any]]:
    t0 = time.time()
    _verify_outbound_url(url)
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) OR-Free/2.0",
        "Accept": "application/json",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    with _urlopen_no_redirect(req, timeout=timeout) as resp:
        elapsed_ms = (time.time() - t0) * 1000.0
        body = resp.read().decode("utf-8")
        return resp.status, elapsed_ms, json.loads(body)


def fetch_quota(api_key: str, as_json: bool = False) -> Dict[str, Any]:
    rotate_and_prune_logs()
    status, elapsed_ms, data = http_get_json(f"{OPENROUTER_BASE_URL}/auth/key", api_key)
    info = data.get("data", {})
    free_dr = info.get("free_model_daily_requests") or {}

    if isinstance(free_dr, dict) and free_dr.get("used") is not None:
        with RouterStateLock():
            st = load_router_state()
            st["or_remote_daily_used"] = int(free_dr["used"])
            save_router_state(st, strict=True)

    result = {
        "http_status": status,
        "elapsed_ms": round(elapsed_ms, 1),
        "key_masked": mask_key(api_key),
        "label": info.get("label"),
        "is_free_tier": info.get("is_free_tier"),
        "usage_usd": info.get("usage", 0.0),
        "usage_daily_usd": info.get("usage_daily", 0.0),
        "limit_usd": info.get("limit"),
        "limit_remaining_usd": info.get("limit_remaining"),
        "free_model_daily_requests": free_dr,
    }
    if as_json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"=== OpenRouter Key Quota Status (HTTP {status}, {result['elapsed_ms']} ms) ===")
        print(f"  Key (Masked)           : {result['key_masked']}")
        print(f"  Is Free Tier           : {result['is_free_tier']}")
        print(f"  Total Usage (USD)      : ${result['usage_usd']:.6f} (Daily: ${result['usage_daily_usd']:.6f})")
        print(f"  Budget Limit (USD)     : ${result['limit_usd']} (Remaining: ${result['limit_remaining_usd']})")
        if isinstance(free_dr, dict) and free_dr:
            print(
                f"  Free Model Daily Quota : used={free_dr.get('used')} / limit={free_dr.get('limit')} "
                f"(remaining={free_dr.get('remaining')})"
            )
        else:
            print(f"  Free Model Daily Quota : {free_dr}")
    return result


def list_free_models(api_key: str, as_json: bool = False) -> List[Dict[str, Any]]:
    rotate_and_prune_logs()
    status, elapsed_ms, data = http_get_json(f"{OPENROUTER_BASE_URL}/models", api_key)
    models = data.get("data", [])
    free_models = []
    for m in models:
        mid = m.get("id", "")
        pricing = m.get("pricing") or {}
        is_zero_price = (
            str(pricing.get("prompt", "1")) in ("0", "0.0")
            and str(pricing.get("completion", "1")) in ("0", "0.0")
            and str(pricing.get("request", "0")) in ("0", "0.0")
        )
        if mid.endswith(":free") or mid == "openrouter/free" or is_zero_price:
            free_models.append({
                "id": mid,
                "name": m.get("name", ""),
                "context_length": m.get("context_length", 0),
                "pricing": {
                    "prompt": pricing.get("prompt"),
                    "completion": pricing.get("completion"),
                },
            })

    free_models.sort(key=lambda x: (-int(x.get("context_length") or 0), x["id"]))
    if as_json:
        print(json.dumps({
            "http_status": status,
            "elapsed_ms": round(elapsed_ms, 1),
            "count": len(free_models),
            "models": free_models,
        }, indent=2, ensure_ascii=False))
    else:
        print(f"=== OpenRouter Available :free Models (HTTP {status}, {elapsed_ms:.1f} ms, total={len(free_models)}) ===")
        for idx, fm in enumerate(free_models[:35], 1):
            print(f"  {idx:2d}. {fm['id']:50s} | ctx: {fm['context_length']:8d} | name: {fm['name']}")
        if len(free_models) > 35:
            print(f"  ... and {len(free_models) - 35} more free models.")
    return free_models


def show_routes(as_json: bool = False) -> None:
    rotate_and_prune_logs()
    state = load_router_state()
    now = time.time()

    providers_report = {}
    for pid, pcfg in PROVIDERS.items():
        p_ts = prune_60s(state.get("provider_rpm_timestamps", {}).get(pid, []), now)
        p_rpd = state.get("provider_rpd_counts", {}).get(pid, 0)
        if pid == "openrouter" and state.get("or_remote_daily_used") is not None:
            p_rpd = max(p_rpd, int(state["or_remote_daily_used"]))
        providers_report[pid] = {
            "name": pcfg["name"],
            "keyless": pcfg["keyless"],
            "rpm_current": len(p_ts),
            "rpm_watermark": pcfg["rpm_watermark"],
            "rpm_hard_limit": pcfg["rpm_limit"],
            "rpd_current": p_rpd,
            "rpd_watermark": pcfg["rpd_watermark"],
            "rpd_hard_limit": pcfg["rpd_limit"],
            "model_rpd_watermark": pcfg["model_rpd_watermark"],
            "model_rpd_hard_limit": pcfg["model_rpd_limit"],
            "status": "PROACTIVE_REDIRECT" if (len(p_ts) >= pcfg["rpm_watermark"] or p_rpd >= pcfg["rpd_watermark"]) else "HEALTHY",
        }

    models_report = {}
    all_cands = set()
    for tpool in TASK_POOLS.values():
        for c in tpool["fallback_chain"]:
            all_cands.add(normalize_candidate_spec(c)[2])
    for cid in sorted(all_cands):
        gate = check_candidate_gate(cid, state, now)
        m_ts = prune_60s(state.get("model_rpm_timestamps", {}).get(cid, []), now)
        m_rpd = state.get("model_rpd_counts", {}).get(cid, 0)
        st = state.get("stats", {}).get(cid, {})
        models_report[cid] = {
            "status": "READY" if gate is None else f"SKIP ({gate})",
            "rpm_60s": len(m_ts),
            "rpd_today": m_rpd,
            "succ": st.get("succ", 0),
            "fail": st.get("fail", 0),
            "avg_ms": st.get("avg_ms", 0.0),
        }

    if as_json:
        print(json.dumps({
            "state_file": str(get_state_path()),
            "log_dir": str(get_logs_dir()),
            "day_utc": state.get("day_utc"),
            "rr_counters": state.get("rr_counters"),
            "providers": providers_report,
            "tasks": TASK_POOLS,
            "models": models_report,
            "registry_path": _REGISTRY_LOADED_FROM,
            "registry_updated_at": _REGISTRY_UPDATED_AT,
            "registry_age_hours": _REGISTRY_AGE_HOURS,
            "registry_sha256": _REGISTRY_SHA256,
            "verified_zero_count": len(VERIFIED_ZERO_PRICE_IDS),
            "curated_keyless_count": len(CURATED_KEYLESS_IDS),
        }, indent=2, ensure_ascii=False))
        return

    print("=== OR-Free + OB-1 Multi-Provider Proactive Redundancy Router ===")
    print(f"  State File : {get_state_path()} (UTC Day: {state.get('day_utc')})")
    print(f"  Audit Logs : {get_logs_dir()} (24h active .jsonl -> next-day .zip -> 30d auto-delete)")
    print(f"  DLP Guard  : ENABLED (blocks PEM private keys, sk-or-v1/AKIA/ghp tokens, [SECRET]/[涉密])")
    print(f"  Cost Guard : ENABLED (pricing_verified_models + curated keyless; exit 2 on unverified)")
    print(f"  Registry   : {_REGISTRY_LOADED_FROM or '(none)'}")
    print(f"  Updated at : {_REGISTRY_UPDATED_AT or 'n/a'}  age_hours={_REGISTRY_AGE_HOURS if _REGISTRY_AGE_HOURS is not None else 'n/a'}")
    print(f"  Verified $0: {len(VERIFIED_ZERO_PRICE_IDS)}  keyless={len(CURATED_KEYLESS_IDS or KEYLESS_ALLOWLIST)}")
    if _REGISTRY_SHA256:
        print(f"  SHA256     : {_REGISTRY_SHA256[:16]}...")
    print("\n--- 1. Provider Proactive Watermarks (Switches BEFORE 429) ---")
    for pid, pr in providers_report.items():
        pkey = load_provider_key(pid)
        mode = "Keyless (0-setup)" if pr["keyless"] else (f"Keyed ({mask_key(pkey)})" if pkey else f"No Key ({PROVIDERS[pid].get('key_env','KEY')})")
        print(
            f"  • {pid:10s} [{pr['status']:18s}] | {mode:24s} | "
            f"RPM: {pr['rpm_current']:2d}/{pr['rpm_watermark']:2d} (lim {pr['rpm_hard_limit']:2d}) | "
            f"RPD: {pr['rpd_current']:3d}/{pr['rpd_watermark']:3d} (lim {pr['rpd_hard_limit']:4d}) | "
            f"Model RPD WM: {pr['model_rpd_watermark']}/{pr['model_rpd_hard_limit']}"
        )

    print("\n--- 2. Task-Aware Interleaved Schedules & Multi-Provider Fallback Chains ---")
    for tname, tcfg in TASK_POOLS.items():
        rr = state.get("rr_counters", {}).get(tname, 0)
        print(f"  [{tname.upper()}] (next rotation seq={rr}) — {tcfg['description']}")
        print(f"    Interleave Rotation : {'  ⇄  '.join(tcfg['interleave_schedule'])}")
        print(f"    Full Fallback Chain : {'  →  '.join(tcfg['fallback_chain'][:7])} ...")

    print("\n--- 3. Live Candidate Telemetry & Gate Status ---")
    for cid, mr in models_report.items():
        print(
            f"  • {cid:55s} | {mr['status']:32s} | "
            f"60s:{mr['rpm_60s']:2d} | day:{mr['rpd_today']:3d} | "
            f"ok:{mr['succ']:2d} err:{mr['fail']:2d} | avg:{mr['avg_ms']:6.1f}ms"
        )


# ── Dynamic Grok Ultra Quota Policy Gate ──────────────────────────────────────

TIER_CONSERVATIVE_RANK: Dict[str, int] = {
    "red": 0,
    "yellow": 1,
    "blue": 2,
    "green": 3,
}


def get_quota_policy_path(explicit: Optional[Path] = None) -> Path:
    if explicit is not None:
        return Path(explicit).expanduser()
    env_p = (os.environ.get("OR_FREE_QUOTA_POLICY_PATH") or "").strip()
    if env_p:
        return Path(env_p).expanduser()
    return get_app_home() / "quota_policy.json"


def _parse_iso_utc(ts_raw: Any) -> Optional[datetime]:
    if not ts_raw or not isinstance(ts_raw, str):
        return None
    s = ts_raw.strip()
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


DEFAULT_CYCLE_PERIOD = timedelta(days=7)


def rollover_quota_policy(
    policy: Dict[str, Any],
    now: Optional[datetime] = None,
) -> Tuple[Dict[str, Any], bool]:
    """Roll an expired cycle forward to the cycle containing `now` (0-token, local).

    Live usage is NOT known for the new cycle, so usage.status becomes "unknown"
    and runtime.tier falls back to the conservative known default "blue".
    Returns (policy, rolled). Never raises; on bad data returns (policy, False).
    """
    try:
        now_dt = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        cycle = policy.get("cycle") if isinstance(policy.get("cycle"), dict) else None
        if cycle is None:
            return policy, False
        starts_at = _parse_iso_utc(cycle.get("starts_at"))
        reset_at = _parse_iso_utc(cycle.get("reset_at"))
        if reset_at is None or now_dt < reset_at:
            return policy, False
        period = (reset_at - starts_at) if (starts_at is not None and reset_at > starts_at) else DEFAULT_CYCLE_PERIOD
        if period.total_seconds() < 3600:
            period = DEFAULT_CYCLE_PERIOD
        n = int((now_dt - reset_at).total_seconds() // period.total_seconds()) + 1
        new_start = reset_at + period * (n - 1)
        new_reset = new_start + period
        fmt = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")
        now_s = fmt(now_dt)
        new_p = json.loads(json.dumps(policy))
        old_cycle_id = cycle.get("cycle_id") or cycle.get("starts_at")
        old_usage = policy.get("usage") if isinstance(policy.get("usage"), dict) else {}
        new_p["cycle"]["cycle_id"] = fmt(new_start)
        new_p["cycle"]["starts_at"] = fmt(new_start)
        new_p["cycle"]["reset_at"] = fmt(new_reset)
        new_p["usage"] = {
            "status": "unknown",
            "plan": old_usage.get("plan"),
            "source": "lazy_rollover_no_live_usage",
            "sample_id": None,
            "observed_at": None,
            "used_fraction": None,
            "remaining_fraction": None,
            "max_age_seconds": old_usage.get("max_age_seconds", 600),
            "unknown_since": now_s,
            "previous_sample_id": old_usage.get("sample_id"),
        }
        rt = new_p.get("runtime") if isinstance(new_p.get("runtime"), dict) else {}
        rt.update({"tier": "blue", "tier_since": now_s, "candidate_tier": None, "candidate_fresh_sample_count": 0})
        new_p["runtime"] = rt
        try:
            new_p["revision"] = int(policy.get("revision") or 0) + 1
        except Exception:
            new_p["revision"] = 1
        new_p["writer_id"] = "or_free_lazy_rollover"
        new_p["generated_at"] = now_s
        # usage unknown => no live-snapshot freshness to expire; valid for the new cycle
        new_p["valid_until"] = fmt(new_reset)
        new_p["rollover"] = {
            "rolled_at": now_s,
            "from_cycle_id": old_cycle_id,
            "periods_advanced": n,
            "usage_unknown": True,
            "fallback_tier": "blue",
            "note": "cycle reset passed; live usage unavailable -> conservative blue routing",
        }
        return new_p, True
    except Exception:
        return policy, False


def _atomic_write_policy(target: Path, policy: Dict[str, Any]) -> bool:
    try:
        tmp = target.with_name(target.name + f".tmp.{os.getpid()}")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(policy, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
        return True
    except Exception:
        return False


def load_quota_policy(policy_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Loader for quota_policy.json ($OR_FREE_QUOTA_POLICY_PATH, else $OR_FREE_HOME|$XDG_CONFIG_HOME/or-free|~/.config/or-free). Never crashes; missing file -> None (POLICY_UNAVAILABLE, calls proceed as before).

    Lazy 0-token refresh: if the quota cycle reset has passed, the cycle is rolled
    forward (usage -> unknown, tier -> blue) and persisted atomically, keeping a
    one-time backup of the pre-rollover file. OR_FREE_POLICY_NO_ROLLOVER=1 disables the write-back (in-memory rollover still applies).
    """
    target = get_quota_policy_path(policy_path)
    try:
        if not target.is_file():
            return None
        raw = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return None
        if "cycle" not in raw or "usage" not in raw or "runtime" not in raw:
            return None
    except Exception:
        return None
    if (os.environ.get("OR_FREE_POLICY_NO_ROLLOVER") or "").strip() not in ("1", "true", "yes"):
        rolled_p, rolled = rollover_quota_policy(raw)
        if rolled:
            try:
                bak = target.with_name(target.name + ".pre-rollover." + str(raw.get("revision")))
                if not bak.exists():
                    bak.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
            except Exception:
                pass
            _atomic_write_policy(target, rolled_p)
            return rolled_p
    return raw


def effective_tier(
    policy: Optional[Dict[str, Any]],
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Compute effective quota tier with reserve deduction, time progress, and expiry fallback."""
    now_dt = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if not isinstance(policy, dict):
        return {
            "tier": "unknown",
            "fallback_tier": "blue",
            "raw_tier": "unknown",
            "policy_revision": None,
            "status": "policy_unavailable",
            "reason": "policy_missing_or_corrupt",
            "usage_unknown": True,
            "used_fraction": None,
            "remaining_fraction": None,
            "beta": None,
            "surplus": None,
            "hours_to_reset": None,
            "valid_until": None,
        }

    revision = policy.get("revision")
    runtime = policy.get("runtime") if isinstance(policy.get("runtime"), dict) else {}
    raw_tier = str(runtime.get("tier") or "blue").strip().lower()
    if raw_tier not in TIER_CONSERVATIVE_RANK:
        raw_tier = "blue"

    cycle = policy.get("cycle") if isinstance(policy.get("cycle"), dict) else {}
    usage = policy.get("usage") if isinstance(policy.get("usage"), dict) else {}
    thresholds = policy.get("thresholds") if isinstance(policy.get("thresholds"), dict) else {}

    starts_at = _parse_iso_utc(cycle.get("starts_at"))
    reset_at = _parse_iso_utc(cycle.get("reset_at"))
    valid_until = _parse_iso_utc(policy.get("valid_until"))
    observed_at = _parse_iso_utc(usage.get("observed_at"))

    rolled_over = False
    if reset_at is not None and now_dt >= reset_at:
        # Old behaviour returned status=cycle_expired (=> BYPASS_TO_PRIMARY forever until
        # someone rewrote the file). Now roll forward in memory to the current cycle.
        rolled_p, rolled_over = rollover_quota_policy(policy, now_dt)
        if not rolled_over:
            return {
                "tier": "blue",
                "fallback_tier": "blue",
                "raw_tier": raw_tier,
                "policy_revision": revision,
                "status": "cycle_expired",
                "reason": "cycle_reset_passed_blue_unknown",
                "usage_unknown": True,
                "used_fraction": None,
                "remaining_fraction": None,
                "beta": None,
                "surplus": None,
                "hours_to_reset": 0.0,
                "valid_until": policy.get("valid_until"),
            }
        policy = rolled_p
        revision = policy.get("revision")
        raw_tier = "blue"
        cycle = policy["cycle"]
        usage = policy["usage"]
        starts_at = _parse_iso_utc(cycle.get("starts_at"))
        reset_at = _parse_iso_utc(cycle.get("reset_at"))
        valid_until = _parse_iso_utc(policy.get("valid_until"))
        observed_at = None

    usage_status = str(usage.get("status") or "").strip().lower()
    try:
        used_f = float(usage.get("used_fraction"))
    except Exception:
        used_f = -1.0

    if usage_status != "known" or not math.isfinite(used_f) or used_f < 0.0 or used_f > 1.0:
        cons_tier = raw_tier if TIER_CONSERVATIVE_RANK[raw_tier] <= TIER_CONSERVATIVE_RANK["blue"] else "blue"
        return {
            "tier": cons_tier,
            "fallback_tier": cons_tier,
            "raw_tier": raw_tier,
            "policy_revision": revision,
            "status": "usage_unknown",
            "reason": "new_cycle_usage_unknown_default_blue" if (rolled_over or isinstance(policy.get("rollover"), dict)) else "usage_unknown_default_conservative_tier",
            "usage_unknown": True,
            "used_fraction": None,
            "remaining_fraction": None,
            "beta": None,
            "surplus": None,
            "hours_to_reset": round(max(0.0, (reset_at - now_dt).total_seconds() / 3600.0), 2) if reset_at else None,
            "valid_until": policy.get("valid_until"),
            "cycle_reset_at": cycle.get("reset_at"),
        }

    R = max(0.0, min(1.0, 1.0 - used_f))
    if reset_at is not None and starts_at is not None and reset_at > starts_at:
        h = max(0.0, (reset_at - now_dt).total_seconds() / 3600.0)
        L = max(1e-6, (reset_at - starts_at).total_seconds() / 3600.0)
    else:
        h = 24.0
        L = 168.0
    T = max(0.0, min(1.0, h / L))

    reserve_base = float(thresholds.get("reserve_fraction", 0.05))
    release_h = float(thresholds.get("reserve_release_hours", 12.0))
    F = reserve_base * min(1.0, h / release_h) if release_h > 0 else reserve_base
    A = max(R - F, 0.0)
    U = (1.0 - F) * T
    surplus = A - U
    beta = (A / U) if U > 1e-9 else (999.0 if A > 0 else 0.0)

    red_beta = float(thresholds.get("red_beta", 0.60))
    yellow_beta = float(thresholds.get("yellow_beta", 0.90))
    green_beta = float(thresholds.get("green_beta", 1.25))
    clearance_h = float(thresholds.get("clearance_hours", 24.0))
    green_surplus = float(thresholds.get("green_surplus_fraction", 0.05))

    if R <= F or A < red_beta * U:
        nominal = "red"
    elif A < yellow_beta * U:
        nominal = "yellow"
    elif A >= green_beta * U and (h <= clearance_h or surplus >= green_surplus):
        nominal = "green"
    else:
        nominal = "blue"

    # Consumer may tighten tier if nominal is more conservative than controller runtime.tier
    eff_tier = nominal if TIER_CONSERVATIVE_RANK[nominal] < TIER_CONSERVATIVE_RANK[raw_tier] else raw_tier

    try:
        max_age_s = float(usage.get("max_age_seconds", 600))
    except Exception:
        max_age_s = 600.0
    age_s = (now_dt - observed_at).total_seconds() if observed_at is not None else 999999.0
    is_expired = (valid_until is not None and now_dt > valid_until) or (age_s > max_age_s)

    if is_expired:
        # Same-cycle expiry rule: never stay green; keep more conservative of previous and blue
        eff_tier = eff_tier if TIER_CONSERVATIVE_RANK[eff_tier] <= TIER_CONSERVATIVE_RANK["blue"] else "blue"
        status = "stale_same_cycle"
        reason = "stale_policy_fallback_to_conservative_blue"
    else:
        status = "fresh"
        reason = "fresh_policy_surplus" if eff_tier == "green" else f"fresh_policy_tier_{eff_tier}"

    return {
        "tier": eff_tier,
        "fallback_tier": eff_tier,
        "raw_tier": raw_tier,
        "nominal_tier": nominal,
        "policy_revision": revision,
        "status": status,
        "reason": reason,
        "usage_unknown": False,
        "used_fraction": round(used_f, 4),
        "remaining_fraction": round(R, 4),
        "reserve_fraction": round(F, 4),
        "beta": round(beta, 2),
        "surplus": round(surplus, 4),
        "hours_to_reset": round(h, 2),
        "valid_until": policy.get("valid_until"),
    }


def check_policy_decision(
    task_class: Optional[str] = None,
    sensitivity: str = "public",
    estimated_tokens: int = 0,
    policy: Optional[Dict[str, Any]] = None,
    policy_path: Optional[Path] = None,
    allow_green_batch: bool = False,
) -> Dict[str, Any]:
    """Evaluate quota_policy.json and return routing decision (BYPASS_TO_PRIMARY / USE_FREE / POLICY_UNAVAILABLE)."""
    resolved_path = get_quota_policy_path(policy_path)
    loaded = policy if policy is not None else load_quota_policy(resolved_path)
    view = effective_tier(loaded)

    routing = (loaded or {}).get("routing") if isinstance((loaded or {}).get("routing"), dict) else {}
    allowlist = set(routing.get("free_allowlist") or [
        "public_text_classification",
        "public_document_extraction",
        "sanitized_log_preprocessing",
        "public_summary",
        "verified_low_risk_code_candidate",
    ])
    denylist = set(routing.get("free_denylist") or [
        "restricted_data",
        "security_sensitive_code",
        "complex_reasoning",
        "production_change_plan",
        "high_impact_final_decision",
    ])
    free_target_map = routing.get("free_target") if isinstance(routing.get("free_target"), dict) else {
        "green": 0.0,
        "blue": 0.35,
        "yellow": 0.65,
        "red": 0.90,
    }

    sens_clean = (sensitivity or "public").strip().lower()
    tc_clean = (task_class or "").strip()

    restricted_sens = sens_clean in (
        "restricted", "secret", "confidential", "internal", "private", "pii", "credentials"
    )
    denied_task = bool(tc_clean and tc_clean in denylist)
    external_free_allowed = not (restricted_sens or denied_task)

    if loaded is None:
        decision = "POLICY_UNAVAILABLE"
        tier = "unknown"
        reason = "policy_missing_or_corrupt"
        free_target = free_target_map.get("blue", 0.35)
    elif not external_free_allowed:
        decision = "BYPASS_TO_PRIMARY"
        tier = view["tier"]
        reason = "denylist_or_restricted_sensitivity"
        free_target = 0.0
    elif view["status"] == "cycle_expired":
        # Only reachable when rollover is impossible/disabled (OR_FREE_POLICY_NO_ROLLOVER=1)
        decision = "BYPASS_TO_PRIMARY"
        tier = view["tier"]
        reason = view["reason"]
        free_target = free_target_map.get(tier, 0.35)
    elif view["status"] == "usage_unknown":
        # Live usage unknown: keep the unknown flag, but route at the conservative
        # known default tier (blue 35% or tighter) instead of bypassing everything.
        tier = view["tier"]
        free_target = float(free_target_map.get(tier, 0.35))
        if tc_clean and tc_clean not in allowlist and tc_clean not in ("auto", "general", "code", "long", "fast"):
            decision = "BYPASS_TO_PRIMARY"
            reason = f"task_class_not_in_free_allowlist:{tc_clean}"
        else:
            decision = "USE_FREE"
            reason = view["reason"]
    elif view["tier"] == "green":
        tier = "green"
        free_target = float(free_target_map.get("green", 0.0))
        if allow_green_batch and (not tc_clean or tc_clean in allowlist):
            decision = "USE_FREE"
            reason = "green_batch_exception_allowlisted"
        else:
            decision = "BYPASS_TO_PRIMARY"
            reason = view["reason"]
    else:
        tier = view["tier"]
        free_target = float(free_target_map.get(tier, 0.35))
        if tc_clean and tc_clean not in allowlist and tc_clean not in ("auto", "general", "code", "long", "fast"):
            decision = "BYPASS_TO_PRIMARY"
            reason = f"task_class_not_in_free_allowlist:{tc_clean}"
        else:
            decision = "USE_FREE"
            reason = view["reason"]

    return {
        "decision": decision,
        "tier": tier,
        "policy_revision": view["policy_revision"],
        "reason": reason,
        "external_free_allowed": external_free_allowed,
        "free_target": free_target,
        "status": view["status"],
        "usage_unknown": bool(view.get("usage_unknown")),
        "used_fraction": view.get("used_fraction"),
        "remaining_fraction": view.get("remaining_fraction"),
        "beta": view.get("beta"),
        "surplus": view.get("surplus"),
        "hours_to_reset": view.get("hours_to_reset"),
        "task_class": tc_clean or "auto",
        "sensitivity": sens_clean,
        "estimated_tokens": int(estimated_tokens or 0),
        "policy_path": str(resolved_path),
        "valid_until": view.get("valid_until"),
    }


# ── Core Chat Completion Dispatch with Proactive Watermark + Failover ─────────

def call_single_candidate(
    canonical_id: str,
    api_key: str,
    messages: List[Dict[str, str]],
    max_tokens: int,
    temperature: float,
    timeout: int,
) -> Tuple[int, float, Dict[str, Any], str]:
    platform, model_id, _ = normalize_candidate_spec(canonical_id)
    p_cfg = PROVIDERS[platform]
    url = f"{p_cfg['base_url']}/chat/completions"
    # Gate 1: refuse non-HTTPS / non-whitelisted hosts (no redirects followed by caller)
    _verify_outbound_url(url)
    if not is_safe_free_target(platform, model_id):
        raise RuntimeError(f"HARD_COST_LOCK: Refusing HTTP to non-verified-free model '{canonical_id}'")

    payload = {
        "model": model_id,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) OB-1/0.3.15",
    }
    if not p_cfg["keyless"]:
        eff_key = load_provider_key(platform) or (api_key if platform == "openrouter" else "")
        if not eff_key:
            raise RuntimeError(f"Missing API key for keyed platform {platform}")
        if platform == "openrouter":
            m_clean = model_id.strip().lower()
            if not (m_clean.endswith(":free") or m_clean == "openrouter/free"):
                raise RuntimeError(f"HARD_COST_LOCK: Refusing to send HTTP request to paid OpenRouter model '{model_id}'")
        headers["Authorization"] = f"Bearer {eff_key}"
        headers["HTTP-Referer"] = "https://kilocode.ai"
        headers["X-Title"] = "Kilo Code"

    t0 = time.time()
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    # Hard wall-clock deadline wrapper (prevents slow-drip keepalive streams from hanging for minutes)
    box_res: Dict[str, Any] = {}
    def _do_http() -> None:
        try:
            with _urlopen_no_redirect(req, timeout=timeout) as r:
                box_res["status"] = r.status
                box_res["headers"] = r.headers
                box_res["raw_body"] = r.read().decode("utf-8")
        except Exception as exc:
            box_res["exc"] = exc

    worker = threading.Thread(target=_do_http, daemon=True)
    worker.start()
    worker.join(timeout=timeout)
    elapsed_ms = (time.time() - t0) * 1000.0
    if worker.is_alive():
        raise TimeoutError(f"Hard wall-clock timeout ({timeout}s) exceeded for {canonical_id}")
    if "exc" in box_res:
        raise box_res["exc"]

    if True:
        resp_status = int(box_res["status"])
        resp_headers = box_res["headers"]
        raw_body = box_res["raw_body"]
        data = json.loads(raw_body)
        if "error" in data and data["error"]:
            err_obj = data["error"]
            code = int(err_obj.get("code") or 500)
            raise urllib.error.HTTPError(url, code, str(err_obj.get("message", "Provider error")), resp_headers, None)

        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError(f"Empty choices returned from {canonical_id}")
        msg_obj = choices[0].get("message") or {}
        content = msg_obj.get("content") or ""
        if not content and msg_obj.get("reasoning_content"):
            content = msg_obj.get("reasoning_content")
        elif not content and msg_obj.get("reasoning"):
            content = msg_obj.get("reasoning")
        if not str(content).strip():
            raise RuntimeError(f"Blank text content returned from {canonical_id}")
        return resp_status, elapsed_ms, data, str(content)


def chat_completion(
    api_key: str,
    prompt: str,
    system_prompt: Optional[str] = None,
    requested_model: Optional[str] = None,
    task: str = "auto",
    prefer_provider: Optional[str] = None,
    allow_paid: bool = False,
    max_tokens: int = 1024,
    temperature: float = 0.2,
    as_json: bool = False,
    rpm_watermark_override: Optional[int] = None,
    model_rpd_watermark_override: Optional[int] = None,
    no_fallback: bool = False,
    caller: Optional[str] = None,
    task_class: Optional[str] = None,
    sensitivity: str = "public",
    estimated_tokens: int = 0,
    allow_green_batch: bool = False,
    policy_path: Optional[Path] = None,
) -> Dict[str, Any]:
    resolved_caller = detect_caller(caller)
    resolved_task = detect_task_type(prompt, task)

    # 1. DLP / Secret check BEFORE any network activity
    combined_input = f"{system_prompt or ''}\n{prompt}"
    secret_hit = check_sensitive_content(combined_input)
    if secret_hit:
        append_audit_log(
            caller=resolved_caller,
            task=resolved_task,
            route="LOCAL_DLP_GUARD",
            status=f"BLOCKED_DLP:{secret_hit}",
            elapsed_ms=0.0,
            tokens_in=0,
            tokens_out=0,
            prompt_text=prompt,
            reply_text="Blocked by local DLP guard; route to Grok Bot native brain",
        )
        sys.stderr.write(
            f"[SECURITY / DLP BLOCK] Detected sensitive content ({secret_hit}) in prompt!\n"
            f"Refusing to send confidential/credential data to external free-model routers.\n"
            f"Keep confidential work on your primary local agent; do not send secrets to public free APIs.\n"
        )
        sys.exit(3)

    # 2. Validate requested model safety if explicitly provided
    if requested_model:
        validate_model_safety(
            requested_model,
            allow_paid=allow_paid,
            caller=resolved_caller,
            task=resolved_task,
            prompt_text=prompt,
        )

    # 2.5 Check Grok Ultra quota policy BEFORE sending any external free HTTP request
    est_tok = estimated_tokens if estimated_tokens > 0 else max(1, len(combined_input) // 4)
    policy_gate = check_policy_decision(
        task_class=task_class,
        sensitivity=sensitivity,
        estimated_tokens=est_tok,
        policy_path=policy_path,
        allow_green_batch=allow_green_batch,
    )
    if policy_gate.get("decision") == "BYPASS_TO_PRIMARY":
        append_audit_log(
            caller=resolved_caller,
            task=resolved_task,
            route=f"QUOTA_POLICY:{policy_gate.get('tier', 'unknown')}",
            status=f"BYPASS_TO_PRIMARY:{policy_gate.get('reason', '')}",
            elapsed_ms=0.0,
            tokens_in=0,
            tokens_out=0,
            prompt_text=prompt,
            reply_text=f"Bypassed free pool (tier={policy_gate.get('tier')}, rev={policy_gate.get('policy_revision')})",
        )
        if as_json:
            print(json.dumps(policy_gate, indent=2, ensure_ascii=False))
        else:
            print(json.dumps(policy_gate, ensure_ascii=False))
        return policy_gate

    # 3. Resolve ordered multi-provider candidate chain
    state = load_router_state()
    candidates, rotation_slot = build_ordered_candidates(
        resolved_task,
        state,
        explicit_model=requested_model,
        prefer_provider=prefer_provider,
    )
    if no_fallback and requested_model:
        candidates = [normalize_candidate_spec(requested_model)[2]]

    safe_candidates = []
    for cand in candidates:
        platform, model_id, _ = normalize_candidate_spec(cand)
        if is_safe_free_target(platform, model_id):
            safe_candidates.append(cand)
    candidates = safe_candidates
    if not candidates:
        if resolved_task == "long":
            sys.stderr.write(
                "[ERROR] Task pool 'long' is empty (no models with verified context_length >= 200000).\n"
                "Re-run scan_and_sync_free_models.py --apply, or choose -t general/code/fast.\n"
            )
            sys.exit(1)
        sys.stderr.write("[ERROR] No verified free candidates available for this task.\n")
        sys.exit(1)

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    proactive_skips: List[Dict[str, str]] = []
    errors: List[str] = []

    if not _REGISTRY_INITIALIZED:
        _load_dynamic_registry()

    for cand in candidates:
        now = time.time()
        try:
            with RouterStateLock():
                state = load_router_state()
                gate_reason = check_candidate_gate(
                    cand,
                    state,
                    now,
                    rpm_watermark_override=rpm_watermark_override,
                    model_rpd_watermark_override=model_rpd_watermark_override,
                    has_key_override=bool(api_key),
                )
                if gate_reason is not None:
                    proactive_skips.append({"candidate": cand, "reason": gate_reason})
                    continue
                # Reserve RPM/RPD slot BEFORE sending the network request
                reserve_candidate_budget(cand, state, now)
                save_router_state(state, strict=True)
        except Exception as lock_err:
            sys.stderr.write(
                f"[STATE LOCK/PERSIST ERROR] Refusing to send unbudgeted HTTP request for {cand}: {lock_err}\n"
            )
            sys.exit(1)

        platform, model_id, _ = normalize_candidate_spec(cand)
        timeout_sec = 25 if resolved_task != "long" else 40
        t0 = time.time()
        try:
            status, elapsed_ms, data, content = call_single_candidate(
                cand, api_key, messages, max_tokens=max_tokens, temperature=temperature, timeout=timeout_sec
            )
            with RouterStateLock():
                state = load_router_state()
                finalize_attempt_result(cand, state, time.time(), True, status, elapsed_ms)
                save_router_state(state)

            usage = data.get("usage") or {}
            p_tok = int(usage.get("prompt_tokens") or 0)
            c_tok = int(usage.get("completion_tokens") or 0)
            switched_info = None
            if proactive_skips:
                first_skip = proactive_skips[0]
                switched_info = f"{first_skip['candidate']} ({first_skip['reason']})"
            elif errors:
                switched_info = errors[0]

            append_audit_log(
                caller=resolved_caller,
                task=resolved_task,
                route=cand,
                status=status,
                elapsed_ms=elapsed_ms,
                tokens_in=p_tok,
                tokens_out=c_tok,
                prompt_text=prompt,
                reply_text=content,
                switched_from=switched_info,
            )

            result = {
                "http_status": status,
                "elapsed_ms": round(elapsed_ms, 1),
                "caller": resolved_caller,
                "task": resolved_task,
                "rotation_slot": rotation_slot,
                "provider": platform,
                "keyless": PROVIDERS[platform]["keyless"],
                "requested_candidate": cand,
                "actual_model": data.get("model", model_id),
                "id": data.get("id"),
                "proactive_skips": proactive_skips,
                "failover_errors": errors,
                "usage": {
                    "prompt_tokens": p_tok,
                    "completion_tokens": c_tok,
                    "total_tokens": int(usage.get("total_tokens") or (p_tok + c_tok)),
                    "cost_usd": (usage.get("cost") if "cost" in usage else None),
                },
                "billing_policy": (
                    "unverified_override" if _ALLOW_UNVERIFIED_MODEL and platform in ("nvidia", "routeopen")
                    else ("keyless_public_allowlist" if platform in ("ovh", "llm7") else "zero_price_verified")
                ),
                "content": content,
            }
            if as_json:
                print(json.dumps(result, indent=2, ensure_ascii=False))
            else:
                print(content.strip())
            return result

        except urllib.error.HTTPError as e:
            elapsed_ms = (time.time() - t0) * 1000.0
            err_body = ""
            try:
                err_body = e.read().decode("utf-8", errors="replace")[:240]
            except Exception:
                pass
            try:
                with RouterStateLock():
                    state = load_router_state()
                    finalize_attempt_result(cand, state, time.time(), False, e.code, elapsed_ms, err_body)
                    save_router_state(state, strict=True)
            except Exception as persist_err:
                sys.stderr.write(f"[STATE LOCK/PERSIST ERROR] Failed to record HTTP error state: {persist_err}\n")
                sys.exit(1)
            errors.append(f"{cand} -> HTTP {e.code} ({elapsed_ms:.0f}ms)")
            continue
        except Exception as e:
            if "Failed to persist router state" in str(e) or "Failed to acquire exclusive lock" in str(e) or "Failed to open router state lock" in str(e):
                sys.stderr.write(f"[STATE LOCK/PERSIST ERROR] {e}\n")
                sys.exit(1)
            elapsed_ms = (time.time() - t0) * 1000.0
            try:
                with RouterStateLock():
                    state = load_router_state()
                    finalize_attempt_result(cand, state, time.time(), False, 0, elapsed_ms, str(e))
                    save_router_state(state, strict=True)
            except Exception as persist_err:
                sys.stderr.write(f"[STATE LOCK/PERSIST ERROR] Failed to record error state: {persist_err}\n")
                sys.exit(1)
            errors.append(f"{cand} -> {type(e).__name__}: {e} ({elapsed_ms:.0f}ms)")
            continue
    append_audit_log(
        caller=resolved_caller,
        task=resolved_task,
        route="NONE",
        status="EXHAUSTED",
        elapsed_ms=0.0,
        tokens_in=0,
        tokens_out=0,
        prompt_text=prompt,
        reply_text="; ".join(errors)[:60],
    )
    sys.stderr.write(
        f"[ERROR] All candidates exhausted for task='{resolved_task}'.\n"
        f"  Proactive Skips : {json.dumps(proactive_skips, ensure_ascii=False)}\n"
        f"  Failover Errors : {' | '.join(errors)}\n"
    )
    sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="检查 Grok Ultra 额度档位决定是否走免费模型 — Zero-Cost Multi-Provider Router (OpenRouter :free + OB-1 Keyless Kilo/LLM7/OVH) with Quota Policy Gate, Proactive Watermarks, DLP Guard & 24h Auto-Zip Audit Logs."
    )
    parser.add_argument("prompt", nargs="*", help="Prompt text (can also be piped via stdin)")
    parser.add_argument("-m", "--model", default=None, help="Explicit model ID (e.g. openrouter/cohere/north-mini-code:free or llm7/codestral-latest)")
    parser.add_argument("-t", "--task", choices=["auto", "general", "code", "long", "fast"], default="auto", help="Task profile for smart model selection")
    parser.add_argument("-p", "--provider", choices=["openrouter", "kilo", "llm7", "ovh", "nvidia", "routeopen"], default=None, help="Prefer a specific provider pool first")
    parser.add_argument("-s", "--system", default=None, help="Optional system prompt")
    parser.add_argument("--caller", default=None, help="Caller identifier for audit logs (e.g. bot name / script name)")
    parser.add_argument("--max-tokens", type=int, default=1024, help="Max completion tokens")
    parser.add_argument("--temperature", type=float, default=0.2, help="Sampling temperature")
    parser.add_argument("--quota", action="store_true", help="Check OpenRouter key quota, daily free requests, and $0 usage")
    parser.add_argument("--list", action="store_true", help="List available :free models on OpenRouter")
    parser.add_argument("--routes", action="store_true", help="Display proactive watermarks, rotation queues, and per-model telemetry")
    parser.add_argument("--status", action="store_true", help="Display router status, security gates, proactive watermarks, and latest scan summary")
    parser.add_argument("--scan-and-update", action="store_true", help="Run live multi-provider free model scanner & security-gated updater")
    parser.add_argument("--log", "--history", dest="log_limit", nargs="?", const=20, type=int, default=None, help="Show last N audit log records (default 20) & .zip archive status")
    parser.add_argument("--reset-state", action="store_true", help="Reset local router_state.json counters/cooldowns")
    parser.add_argument("--rpm-watermark", type=int, default=None, help="Override provider 60s RPM proactive watermark (for testing/tuning)")
    parser.add_argument("--model-rpd-watermark", type=int, default=None, help="Override per-model daily RPD proactive watermark (for testing/tuning)")
    parser.add_argument("--no-fallback", action="store_true", help="Disable failover when -m/--model is specified")
    parser.add_argument("--json", action="store_true", help="Output structured JSON response with routing telemetry")
    parser.add_argument(
        "--check-policy",
        action="store_true",
        help="检查 Grok Ultra 额度档位策略（读取 quota_policy.json；周期已过会本地惰性续期并原子写回），输出路由决策 JSON 并退出 (exit 0)",
    )
    parser.add_argument(
        "--task-class",
        default=None,
        help="Optional task class for quota policy evaluation (e.g. public_summary, sanitized_log_preprocessing)",
    )
    parser.add_argument(
        "--sensitivity",
        default="public",
        help="Data sensitivity level for quota policy evaluation (default: public; restricted/secret forces BYPASS_TO_PRIMARY)",
    )
    parser.add_argument(
        "--estimated-tokens",
        type=int,
        default=0,
        help="Estimated token workload for quota policy evaluation",
    )
    parser.add_argument(
        "--green-batch-exception",
        action="store_true",
        help="Allow allowlisted batch tasks to use free pool even when tier=green (<=10%% exception cap)",
    )
    parser.add_argument(
        "--policy-path",
        default=None,
        help="Override path to quota_policy.json (default: $OR_FREE_QUOTA_POLICY_PATH, else <OR_FREE_HOME>/quota_policy.json; missing file = no gating)",
    )
    parser.add_argument(
        "--allow-unverified-model",
        action="store_true",
        help="DANGEROUS opt-in: bypass zero-billing proof for nvidia/routeopen/custom models. "
             "Prints a stderr warning; zero cost is NOT guaranteed. Still never allows OpenRouter/Kilo "
             "IDs that lack :free suffix.",
    )
    parser.add_argument("--registry", default=None, help="Explicit registry path (STRICT mode: no fallback; exit 4 on error)")
    parser.add_argument(
        "--validate-registry",
        nargs="?",
        const="__default__",
        default=None,
        metavar="PATH",
        help="Strict-validate a registry JSON and print validation report (exit 0/4)",
    )
    parser.add_argument(
        "--max-registry-age-hours",
        type=str,
        default=None,
        help="Reject catalog price proofs older than N hours (also OR_FREE_MAX_REGISTRY_AGE_HOURS)",
    )

    args = parser.parse_args()

    global _ALLOW_UNVERIFIED_MODEL, _STRICT_REGISTRY_MODE, _EXPLICIT_REGISTRY_PATH, _MAX_REGISTRY_AGE_HOURS
    _ALLOW_UNVERIFIED_MODEL = bool(getattr(args, "allow_unverified_model", False))
    if _ALLOW_UNVERIFIED_MODEL:
        sys.stderr.write(
            "[UNVERIFIED MODEL WARNING] Bypassing zero-billing proof via --allow-unverified-model; "
            "zero cost is NOT guaranteed\n"
        )

    env_max = (os.environ.get("OR_FREE_MAX_REGISTRY_AGE_HOURS") or "").strip()
    if args.max_registry_age_hours is not None:
        _MAX_REGISTRY_AGE_HOURS = _parse_and_validate_max_age_hours(args.max_registry_age_hours, "--max-registry-age-hours")
    elif env_max:
        _MAX_REGISTRY_AGE_HOURS = _parse_and_validate_max_age_hours(env_max, "OR_FREE_MAX_REGISTRY_AGE_HOURS")

    if getattr(args, "registry", None):
        _EXPLICIT_REGISTRY_PATH = str(Path(args.registry).expanduser())
        _STRICT_REGISTRY_MODE = True
    elif (os.environ.get("OR_FREE_REGISTRY_PATH") or "").strip():
        _EXPLICIT_REGISTRY_PATH = str(Path(os.environ["OR_FREE_REGISTRY_PATH"].strip()).expanduser())
        _STRICT_REGISTRY_MODE = True

    if getattr(args, "validate_registry", None) is not None:
        vpath = args.validate_registry
        if vpath == "__default__":
            vpath = str(_EXPLICIT_REGISTRY_PATH or (get_app_home() / "or_free_models_registry.json"))
        validate_registry_cli(vpath)
        return

    # Load registry once AFTER CLI args, TTL policy, and explicit registry path are resolved
    _load_dynamic_registry()

    if getattr(args, "check_policy", False):
        p_path = Path(args.policy_path).expanduser() if getattr(args, "policy_path", None) else None
        res = check_policy_decision(
            task_class=getattr(args, "task_class", None),
            sensitivity=getattr(args, "sensitivity", "public"),
            estimated_tokens=getattr(args, "estimated_tokens", 0),
            policy_path=p_path,
            allow_green_batch=bool(getattr(args, "green_batch_exception", False)),
        )
        print(json.dumps(res, indent=2 if args.json else None, ensure_ascii=False))
        sys.exit(0)

    if args.reset_state:
        sp = get_state_path()
        if sp.exists():
            sp.unlink()
        print(f"Reset router state at {sp}")
        return

    if args.log_limit is not None:
        show_audit_logs(limit=args.log_limit, as_json=args.json)
        return

    if args.scan_and_update:
        import subprocess
        scanner_paths = [
            SCRIPT_DIR / "scan_and_sync_free_models.py",
            Path.cwd() / "scan_and_sync_free_models.py",
            get_app_home() / "scan_and_sync_free_models.py",
            Path.home() / ".ob1" / "scan_and_sync_free_models.py",
        ]
        for sp in scanner_paths:
            if sp.is_file():
                cmd = [sys.executable, str(sp), "--apply"]
                if args.json:
                    cmd.append("--json")
                raise SystemExit(subprocess.call(cmd))
        sys.stderr.write("[ERROR] scan_and_sync_free_models.py not found.\n")
        sys.exit(1)

    if args.routes or args.status:
        show_routes(as_json=args.json)
        return

    api_key = load_api_key()

    if args.quota:
        if not api_key:
            sys.stderr.write("[ERROR] OPENROUTER_API_KEY not found.\n")
            sys.exit(1)
        fetch_quota(api_key, as_json=args.json)
        return

    if args.list:
        if not api_key:
            sys.stderr.write("[ERROR] OPENROUTER_API_KEY not found.\n")
            sys.exit(1)
        list_free_models(api_key, as_json=args.json)
        return

    stdin_text = ""
    if not sys.stdin.isatty():
        stdin_text = sys.stdin.read().strip()

    cli_prompt = " ".join(args.prompt).strip()
    if cli_prompt and stdin_text:
        full_prompt = f"{cli_prompt}\n\n---\n{stdin_text}"
    elif cli_prompt:
        full_prompt = cli_prompt
    elif stdin_text:
        full_prompt = stdin_text
    else:
        parser.print_help()
        sys.exit(1)

    chat_completion(
        api_key=api_key,
        prompt=full_prompt,
        system_prompt=args.system,
        requested_model=args.model,
        task=args.task,
        prefer_provider=args.provider,
        allow_paid=False,
        max_tokens=args.max_tokens,
        temperature=args.temperature,
        as_json=args.json,
        rpm_watermark_override=args.rpm_watermark,
        model_rpd_watermark_override=args.model_rpd_watermark,
        no_fallback=args.no_fallback,
        caller=args.caller,
        task_class=getattr(args, "task_class", None),
        sensitivity=getattr(args, "sensitivity", "public"),
        estimated_tokens=getattr(args, "estimated_tokens", 0),
        allow_green_batch=bool(getattr(args, "green_batch_exception", False)),
        policy_path=Path(args.policy_path).expanduser() if getattr(args, "policy_path", None) else None,
    )


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
