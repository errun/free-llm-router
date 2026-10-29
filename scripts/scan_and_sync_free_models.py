#!/usr/bin/env python3
"""
scan_and_sync_free_models.py — Daily high-capability free LLM scanner & registry updater.

Security gates:
  1) Whitelisted HTTPS domains only (redirects rejected)
  2) Strict zero-billing:
       - OpenRouter & Kilo: live catalog pricing.prompt == 0 AND pricing.completion == 0
         (evidence stored in pricing_verified_models). Kilo seeds NEVER re-add catalog-rejected IDs.
       - OVH & LLM7: curated keyless public allowlist only (vendor catalogs use rate-card stubs;
         no API key or billing account attached). Stored in curated_keyless_allowlist_models.
         We NEVER claim OVH/LLM7 pass catalog $0/$0.
       - NVIDIA/RouteOpen: excluded from default discovery
  3) Anti-poisoning ID/capability filters
  4) Live probe must return exact content == "42" (after strip/rstrip("."))
  5) Atomic write + Gate-5 self-test with RegistryUpdateLock; rollback restores ONLY
     targets replaced this run from THIS run's unique bak (never stale fixed .bak);
     newly created files are unlinked; production replace preserves POSIX mode (default
     0o600); unique bak pruned to last 3 after successful commit. Local budget skips
     use probe_state=skipped_budget.
"""

from __future__ import annotations

import argparse
import concurrent.futures
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

TZ_UTC8 = timezone(timedelta(hours=8))
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
import or_free

WHITELISTED_DOMAINS = frozenset({
    "openrouter.ai",
    "api.kilo.ai",
    "api.llm7.io",
    "oai.endpoints.kepler.ai.cloud.ovh.net",
    "hashneuron.space",
    "integrate.api.nvidia.com",
})
SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9._:/-]+$")
EXCLUDED_KEYWORDS = (
    "content-safety", "qwen3guard", "llama-guard", "moderation",
    "embed", "whisper", "tts", "rerank",
)

# Curated keyless allowlist (OVH/LLM7 only — NOT a price proof)
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

# Known free-named Kilo IDs that MAY be probed ONLY if catalog proves $0/$0
KILO_SEED_CANDIDATES = frozenset({
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

CAPABILITY_META: Dict[str, Dict[str, Any]] = {
    "openrouter/thinkingmachines/inkling:free": {"rank": 28, "tier": ["code", "general", "long"], "note": "975B-A41B MoE Flagship (1.05M ctx)", "context_length": 1050000},
    "openrouter/thinkingmachines/inkling-small:free": {"rank": 31, "tier": ["code", "general", "long", "fast"], "note": "276B-A12B MoE", "context_length": 1050000},
    "openrouter/dots-studio/dots-3-note-preview:free": {"rank": 33, "tier": ["general", "code", "long", "fast"], "note": "280B-A16B Frontier (512K ctx)", "context_length": 512000},
    "kilo/dots-studio/dots-3-note-preview:free": {"rank": 33, "tier": ["general", "code", "long", "fast"], "note": "280B-A16B Frontier (Kilo Keyless, 512K ctx)", "context_length": 512000},
    "kilo/reflection/beam-free": {"rank": 34, "tier": ["general", "code", "long", "fast"], "note": "501B-A23B Frontier Reasoning (1.05M ctx)", "context_length": 1050000},
    "openrouter/nvidia/nemotron-3.5-super-vl:free": {"rank": 36, "tier": ["general", "code", "long", "fast"], "note": "120B-A12B Multimodal Reasoning (1M ctx)", "context_length": 1000000},
    "kilo/nvidia/nemotron-3.5-super-vl:free": {"rank": 36, "tier": ["general", "code", "long", "fast"], "note": "120B-A12B Multimodal Reasoning (Kilo Keyless, 1M ctx)", "context_length": 1000000},
    "openrouter/poolside/laguna-s-2.1:free": {"rank": 57, "tier": ["code", "general", "fast"], "note": "118B-A8B Coding Agent (262K ctx)", "context_length": 262000},
    "kilo/poolside/laguna-s-2.1:free": {"rank": 57, "tier": ["code", "general", "fast"], "note": "118B-A8B Coding Agent (Kilo Keyless, 262K ctx)", "context_length": 262000},
    "openrouter/poolside/laguna-xs-2.1:free": {"rank": 60, "tier": ["code", "fast", "general"], "note": "33B-A3B Coding Agent (262K ctx)", "context_length": 262000},
    "kilo/poolside/laguna-xs-2.1:free": {"rank": 60, "tier": ["code", "fast", "general"], "note": "33B-A3B Coding Agent (Kilo Keyless, 262K ctx)", "context_length": 262000},
    "openrouter/recursive/flint-v1.0:free": {"rank": 62, "tier": ["code", "fast", "general"], "note": "40B-A2.5B MoE Coding Agent (262K ctx)", "context_length": 262000},
    "kilo/recursive/flint-v1.0:free": {"rank": 62, "tier": ["code", "fast", "general"], "note": "40B-A2.5B MoE Coding Agent (Kilo Keyless, 262K ctx)", "context_length": 262000},
    "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free": {"rank": 61, "tier": ["general", "code", "long"], "note": "550B-A55B Ultra MoE", "context_length": 262000},
    "ovh/Qwen3.6-27B": {"rank": 65, "tier": ["code", "general", "fast"], "note": "Qwen3.6-27B (OVH Keyless, 131K ctx)", "context_length": 131000},
    "openrouter/google/gemma-4-31b-it:free": {"rank": 69, "tier": ["general", "code"], "note": "Gemma-4-31B-IT (256K ctx)", "context_length": 256000},
    "ovh/Qwen3.5-397B-A17B": {"rank": 71, "tier": ["general", "code", "long", "fast"], "note": "397B-A17B MoE (OVH Keyless, 262K ctx)", "context_length": 262000},
    "openrouter/google/gemma-4-26b-a4b-it:free": {"rank": 73, "tier": ["general", "fast"], "note": "Gemma-4-26B-A4B MoE (256K ctx)", "context_length": 256000},
    "openrouter/xingchen-agi/xing4.0-29b-a4b:free": {"rank": 75, "tier": ["general", "code"], "note": "Xing4.0-29B-A4B MoE (131K ctx)", "context_length": 131000},
    "llm7/minimax-m2.7": {"rank": 76, "tier": ["code", "general", "fast"], "note": "MiniMax-M2.7 (LLM7 Keyless)"},
    "openrouter/nvidia/nemotron-3.5-lightning:free": {"rank": 81, "tier": ["fast", "general", "long"], "note": "Nemotron-3.5-Lightning (1M ctx)", "context_length": 1000000},
    "kilo/nvidia/nemotron-3.5-lightning:free": {"rank": 81, "tier": ["fast", "general", "long"], "note": "Nemotron-3.5-Lightning (Kilo Keyless, 1M ctx)", "context_length": 1000000},
    "ovh/gpt-oss-120b": {"rank": 83, "tier": ["general", "code", "fast"], "note": "GPT-OSS-120B (OVH Keyless, 131K ctx)", "context_length": 131000},
    "llm7/codestral-latest": {"rank": 85, "tier": ["code", "fast"], "note": "Mistral Codestral Latest (LLM7 Keyless)"},
    "openrouter/cohere/north-mini-code:free": {"rank": 87, "tier": ["code", "fast", "general", "long"], "note": "Cohere North-Mini-Code (256K ctx)", "context_length": 256000},
    "kilo/cohere/north-mini-code:free": {"rank": 87, "tier": ["code", "fast", "general"], "note": "Cohere North-Mini-Code (Kilo Keyless, 256K ctx)", "context_length": 256000},
    "openrouter/nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free": {"rank": 89, "tier": ["general", "fast"], "note": "30B-A3B Reasoning (256K ctx)", "context_length": 256000},
    "kilo/nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free": {"rank": 89, "tier": ["general", "fast"], "note": "30B-A3B Reasoning (Kilo Keyless, 256K ctx)", "context_length": 256000},
    "llm7/nemotron-3-nano:30b": {"rank": 90, "tier": ["fast", "general"], "note": "Nemotron-3-Nano 30B (LLM7 Keyless)"},
    "ovh/Meta-Llama-3_3-70B-Instruct": {"rank": 91, "tier": ["general", "code"], "note": "Llama-3.3-70B-Instruct (OVH Keyless, 131K ctx)", "context_length": 131000},
    "ovh/Qwen2.5-VL-72B-Instruct": {"rank": 92, "tier": ["general"], "note": "Qwen2.5-VL-72B-Instruct (OVH Keyless)"},
    "llm7/gpt-oss:20b": {"rank": 94, "tier": ["fast"], "note": "GPT-OSS-20B (LLM7 Keyless)"},
    "openrouter/openrouter/free": {"rank": 95, "tier": ["general", "code", "long", "fast"], "note": "OpenRouter Auto Free Router (200K ctx)", "context_length": 200000},
    "kilo/kilo-auto/free": {"rank": 96, "tier": ["general", "code", "fast"], "note": "Kilo Auto Free Router"},
    "kilo/openrouter/free": {"rank": 97, "tier": ["general", "code", "fast"], "note": "Kilo OpenRouter Free"},
    "kilo/nvidia/nemotron-3-ultra-550b-a55b:free": {"rank": 61, "tier": ["general", "code", "long"], "note": "550B Ultra (Kilo)", "context_length": 262000},
    "kilo/xingchen-agi/xing4.0-29b-a4b:free": {"rank": 75, "tier": ["general", "code"], "note": "Xing4.0 (Kilo)"},
}


def get_app_home() -> Path:
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
            pass
    p = Path.home() / ".config" / "or-free"
    p.mkdir(parents=True, exist_ok=True)
    return p


def now_str() -> str:
    return datetime.now(TZ_UTC8).strftime("%Y-%m-%d %H:%M:%S UTC+8")


def _parse_price(pricing: dict, key: str, default: float = 1.0) -> float:
    try:
        v = pricing.get(key, None)
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


# Exclude sub-7B dense parameter models (e.g., 0.5b, 1b, 1.5b, 2b, 2.6b, 3b, 4b, 6b) while keeping MoE active-param suffixes like -a3b/-a4b
TINY_MODEL_RE = re.compile(
    r"(?:^|[-_:/])(?:0\.\d+|1(?:\.\d+)?|2(?:\.\d+)?|3(?:\.\d+)?|4(?:\.\d+)?|5(?:\.\d+)?|6(?:\.\d+)?)b(?:$|[-_:/])"
)


def is_safe_model_id(cid: str) -> bool:
    if not isinstance(cid, str) or len(cid) > 120 or not SAFE_ID_RE.match(cid):
        return False
    low = cid.lower()
    if any(k in low for k in EXCLUDED_KEYWORDS):
        return False
    if TINY_MODEL_RE.search(low):
        return False
    return True


class _NoRedirect(urllib.request.HTTPErrorProcessor):
    def http_response(self, request, response):  # type: ignore[no-untyped-def]
        code = response.getcode()
        if 300 <= int(code) < 400:
            raise urllib.error.HTTPError(
                request.full_url, code, "Redirect blocked by Gate 1", response.headers, response
            )
        return super().http_response(request, response)

    https_response = http_response


def _verify_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme.lower() != "https":
        raise RuntimeError(f"Gate1: non-HTTPS {parsed.scheme!r}")
    host = (parsed.hostname or "").lower()
    if host not in WHITELISTED_DOMAINS:
        raise RuntimeError(f"Gate1: non-whitelisted host {host!r}")
    if parsed.port not in (None, 443):
        raise RuntimeError(f"Gate1: non-443 port {parsed.port}")
    if parsed.username or parsed.password:
        raise RuntimeError("Gate1: URL userinfo forbidden")


def fetch_json(url: str, api_key: str = "", timeout: int = 25) -> dict:
    _verify_url(url)
    headers = {"User-Agent": "OR-Free-Scanner/3.0", "Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    opener = urllib.request.build_opener(_NoRedirect)
    with opener.open(req, timeout=timeout) as resp:
        code = resp.getcode()
        if int(code) >= 400:
            raise urllib.error.HTTPError(url, code, f"HTTP {code}", resp.headers, resp)
        return json.loads(resp.read().decode("utf-8"))


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


def load_provider_key(name: str, file_name: Optional[str] = None) -> Optional[str]:
    """Unified key resolution across router and scanner: env -> plain key files -> keys.env."""
    key_id = (name or "").strip().lower()
    if key_id in PROVIDER_KEY_CONFIG:
        env_names = PROVIDER_KEY_CONFIG[key_id]["env_names"]
        file_names = PROVIDER_KEY_CONFIG[key_id]["file_names"]
    elif name == "OPENROUTER_API_KEY":
        env_names = PROVIDER_KEY_CONFIG["openrouter"]["env_names"]
        file_names = PROVIDER_KEY_CONFIG["openrouter"]["file_names"]
    elif name == "ROUTEOPEN_API_KEY":
        env_names = PROVIDER_KEY_CONFIG["routeopen"]["env_names"]
        file_names = PROVIDER_KEY_CONFIG["routeopen"]["file_names"]
    elif name == "NVIDIA_API_KEY":
        env_names = PROVIDER_KEY_CONFIG["nvidia"]["env_names"]
        file_names = PROVIDER_KEY_CONFIG["nvidia"]["file_names"]
    else:
        env_names = (name,) if name else ()
        file_names = (file_name,) if file_name else ()

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

    return None


def load_api_key() -> Optional[str]:
    """Load OpenRouter API key using unified provider key resolution."""
    return load_provider_key("openrouter")


def _kilo_name_ok(mid: str) -> bool:
    m = mid.lower()
    return m.endswith(":free") or m in ("reflection/beam-free", "kilo-auto/free", "openrouter/free")


def discover_catalog_models(
    allow_unverified: bool = False,
) -> Tuple[List[str], List[Dict[str, Any]], Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """Return (discovered, demoted, pricing_verified_models, curated_keyless_models)."""
    discovered: List[str] = []
    demoted: List[Dict[str, Any]] = []
    pricing_verified: Dict[str, Dict[str, Any]] = {}
    curated_keyless: Dict[str, Dict[str, Any]] = {}
    ts = now_str()
    epoch = int(time.time())

    # 1. OpenRouter — live catalog $0/$0 required
    try:
        or_data = fetch_json("https://openrouter.ai/api/v1/models").get("data", [])
        for m in or_data:
            mid = str(m.get("id", ""))
            pricing = m.get("pricing") or {}
            if "prompt" not in pricing or "completion" not in pricing:
                if mid.endswith(":free") or mid == "openrouter/free":
                    demoted.append({"id": f"openrouter/{mid}", "reason": "Gate 2: OpenRouter pricing fields missing"})
                continue
            p_cost = _parse_price(pricing, "prompt", 1.0)
            c_cost = _parse_price(pricing, "completion", 1.0)
            if (mid.endswith(":free") or mid == "openrouter/free") and p_cost == 0.0 and c_cost == 0.0:
                cid = f"openrouter/{mid}"
                if is_safe_model_id(cid):
                    discovered.append(cid)
                    pricing_verified[cid] = {
                        "source": "openrouter_catalog_zero",
                        "prompt_usd": "0",
                        "completion_usd": "0",
                        "verified_at": ts,
                        "verified_epoch": epoch,
                        "context_length": m.get("context_length"),
                    }
                else:
                    demoted.append({"id": cid, "reason": "Gate 3: Excluded classifier/tiny model"})
            elif mid.endswith(":free") or mid == "openrouter/free":
                demoted.append({
                    "id": f"openrouter/{mid}",
                    "reason": f"Gate 2: :free but non-zero pricing prompt={p_cost} completion={c_cost}",
                })
    except Exception as e:
        demoted.append({"id": "openrouter/*", "reason": f"Catalog fetch warning: {e}"})

    # 2. Kilo — MUST have explicit $0/$0 in live catalog; seeds cannot bypass rejection
    kilo_catalog_zero_ids: Set[str] = set()
    kilo_catalog_rejected_ids: Set[str] = set()
    try:
        kilo_data = fetch_json("https://api.kilo.ai/api/gateway/v1/models").get("data", [])
        for m in kilo_data:
            mid = str(m.get("id", ""))
            cid = f"kilo/{mid}"
            pricing = m.get("pricing") or {}
            name_ok = _kilo_name_ok(mid)
            if "prompt" not in pricing or "completion" not in pricing:
                if name_ok:
                    kilo_catalog_rejected_ids.add(cid)
                    demoted.append({"id": cid, "reason": "Gate 2: Kilo pricing fields missing (rejected; seed cannot re-add)"})
                continue
            p_cost = _parse_price(pricing, "prompt", 1.0)
            c_cost = _parse_price(pricing, "completion", 1.0)
            if name_ok and p_cost == 0.0 and c_cost == 0.0:
                if is_safe_model_id(cid):
                    kilo_catalog_zero_ids.add(cid)
                    discovered.append(cid)
                    pricing_verified[cid] = {
                        "source": "kilo_catalog_zero",
                        "prompt_usd": "0",
                        "completion_usd": "0",
                        "verified_at": ts,
                        "verified_epoch": epoch,
                        "context_length": m.get("context_length"),
                    }
                else:
                    demoted.append({"id": cid, "reason": "Gate 3: Excluded"})
            elif name_ok:
                kilo_catalog_rejected_ids.add(cid)
                demoted.append({
                    "id": cid,
                    "reason": f"Gate 2: Kilo free-named but pricing non-zero/missing prompt={p_cost} completion={c_cost} (seed cannot re-add)",
                })
    except Exception as e:
        demoted.append({"id": "kilo/*", "reason": f"Catalog fetch warning: {e}"})

    # Seeds are informational ONLY — never re-add rejected or missing-from-catalog IDs
    for seed in sorted(KILO_SEED_CANDIDATES):
        if seed in kilo_catalog_rejected_ids:
            demoted.append({"id": seed, "reason": "Gate 2: seed blocked — catalog rejected this ID"})
            continue
        if seed not in kilo_catalog_zero_ids:
            demoted.append({"id": seed, "reason": "Gate 2: seed not in live Kilo catalog $0 set — not added"})

    # 3. OVH — curated keyless allowlist only (no catalog $0 claim)
    try:
        ovh_data = fetch_json("https://oai.endpoints.kepler.ai.cloud.ovh.net/v1/models").get("data", [])
        seen_ovh = set()
        for m in ovh_data:
            mid = str(m.get("id", ""))
            cid = f"ovh/{mid}"
            seen_ovh.add(cid)
            if cid in KEYLESS_ALLOWLIST and is_safe_model_id(cid):
                discovered.append(cid)
                curated_keyless[cid] = {
                    "source": "curated_keyless_public_tier",
                    "note": "Keyless public endpoint allowlist (vendor catalog uses rate-card stubs; no API key or billing account attached)",
                    "verified_at": ts,
                }
            else:
                demoted.append({"id": cid, "reason": "Gate 2: OVH model not in curated keyless allowlist"})
        for cid in KEYLESS_ALLOWLIST:
            if cid.startswith("ovh/") and cid not in seen_ovh:
                # still admit curated allowlist even if temporarily missing from catalog listing
                discovered.append(cid)
                curated_keyless[cid] = {
                    "source": "curated_keyless_public_tier",
                    "note": "Keyless public endpoint allowlist (vendor catalog uses rate-card stubs; no API key or billing account attached)",
                    "verified_at": ts,
                }
    except Exception as e:
        demoted.append({"id": "ovh/*", "reason": f"Catalog fetch warning: {e}"})
        for cid in KEYLESS_ALLOWLIST:
            if cid.startswith("ovh/"):
                discovered.append(cid)
                curated_keyless[cid] = {
                    "source": "curated_keyless_public_tier",
                    "note": "Keyless public endpoint allowlist (catalog unreachable; curated allowlist retained)",
                    "verified_at": ts,
                }

    # 4. LLM7 — curated keyless allowlist only
    try:
        llm7_data = fetch_json("https://api.llm7.io/v1/models").get("data", [])
        seen_llm7 = set()
        for m in llm7_data:
            mid = str(m.get("id", ""))
            cid = f"llm7/{mid}"
            seen_llm7.add(cid)
            if cid in KEYLESS_ALLOWLIST and is_safe_model_id(cid):
                discovered.append(cid)
                curated_keyless[cid] = {
                    "source": "curated_keyless_public_tier",
                    "note": "Keyless public endpoint allowlist (vendor catalog uses rate-card stubs; no API key or billing account attached)",
                    "verified_at": ts,
                }
            else:
                demoted.append({"id": cid, "reason": "Gate 2: LLM7 model not in curated keyless allowlist"})
        for cid in KEYLESS_ALLOWLIST:
            if cid.startswith("llm7/") and cid not in seen_llm7:
                discovered.append(cid)
                curated_keyless[cid] = {
                    "source": "curated_keyless_public_tier",
                    "note": "Keyless public endpoint allowlist (vendor catalog uses rate-card stubs; no API key or billing account attached)",
                    "verified_at": ts,
                }
    except Exception as e:
        demoted.append({"id": "llm7/*", "reason": f"Catalog fetch warning: {e}"})
        for cid in KEYLESS_ALLOWLIST:
            if cid.startswith("llm7/"):
                discovered.append(cid)
                curated_keyless[cid] = {
                    "source": "curated_keyless_public_tier",
                    "note": "Keyless public endpoint allowlist (catalog unreachable; curated allowlist retained)",
                    "verified_at": ts,
                }

    demoted.append({
        "id": "routeopen/*,nvidia/*",
        "reason": "Gate 2: disabled by default (not guaranteed $0 for strangers)" + (
            " (--allow-unverified-model set but still excluded from default free pool)" if allow_unverified else ""
        ),
    })

    seen: Set[str] = set()
    unique: List[str] = []
    for c in discovered:
        if c not in seen:
            seen.add(c)
            unique.append(c)
    return unique, demoted, pricing_verified, curated_keyless


def probe_content_ok(text: str) -> bool:
    """Strict: strip whitespace and trailing period only; must equal '42'."""
    clean = (text or "").strip().rstrip(".")
    return clean == "42"


def probe_single_candidate(
    cid: str,
    or_key: Optional[str],
    ro_key: Optional[str],
    enforce_budget: bool = True,
) -> Dict[str, Any]:
    platform, model_id = cid.split("/", 1)
    base_map = {
        "openrouter": "https://openrouter.ai/api/v1",
        "kilo": "https://api.kilo.ai/api/gateway/v1",
        "llm7": "https://api.llm7.io/v1",
        "ovh": "https://oai.endpoints.kepler.ai.cloud.ovh.net/v1",
    }
    base = base_map.get(platform)
    if not base:
        return {"id": cid, "status": 0, "ok": False, "latency_ms": 0.0, "error": "unsupported platform"}
    if platform == "openrouter" and not or_key:
        return {"id": cid, "status": 0, "ok": False, "latency_ms": 0.0, "error": "missing OpenRouter key"}

    url = f"{base}/chat/completions"
    _verify_url(url)

    # Share router budget & exclusive state lock BEFORE sending any probe HTTP request
    if enforce_budget:
        with or_free.RouterStateLock():
            state = or_free.load_router_state()
            now_pre = time.time()
            has_key = bool(or_key if platform == "openrouter" else (ro_key if platform == "routeopen" else True))
            gate_reason = or_free.check_candidate_gate(
                cid,
                state,
                now_pre,
                has_key_override=has_key,
            )
            if gate_reason is not None:
                return {
                    "id": cid,
                    "status": None,
                    "ok": False,
                    "latency_ms": 0.0,
                    "error": f"proactive_budget_gate:{gate_reason}",
                    "probe_state": "skipped_budget",
                }
            or_free.reserve_candidate_budget(cid, state, now_pre)
            or_free.save_router_state(state, strict=True)

    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": "Reply with only the digits 42 and nothing else."}],
        "max_tokens": 16,
        "temperature": 0,
        "stream": False,
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "OR-Free-Scanner/3.0",
    }
    if platform == "openrouter":
        headers["Authorization"] = f"Bearer {or_key}"
        headers["HTTP-Referer"] = "https://kilocode.ai"
        headers["X-Title"] = "OR-Free Scanner"
    t0 = time.time()
    try:
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        opener = urllib.request.build_opener(_NoRedirect)
        with opener.open(req, timeout=45) as resp:
            status = int(resp.getcode())
            raw = resp.read().decode("utf-8")
        latency = (time.time() - t0) * 1000.0
        if status >= 400:
            res: Dict[str, Any] = {"id": cid, "status": status, "ok": False, "latency_ms": latency, "error": f"HTTP {status}"}
        else:
            data = json.loads(raw)
            if data.get("error"):
                res = {"id": cid, "status": status, "ok": False, "latency_ms": latency, "error": str(data["error"])}
            else:
                choices = data.get("choices") or []
                if not choices:
                    res = {"id": cid, "status": status, "ok": False, "latency_ms": latency, "error": "empty choices"}
                else:
                    msg = choices[0].get("message") or {}
                    content = msg.get("content") or ""
                    ok = probe_content_ok(content)
                    res = {"id": cid, "status": status, "ok": ok, "latency_ms": latency, "content": content[:80]}
    except urllib.error.HTTPError as e:
        latency = (time.time() - t0) * 1000.0
        res = {"id": cid, "status": int(e.code), "ok": False, "latency_ms": latency, "error": str(e)}
    except Exception as e:
        latency = (time.time() - t0) * 1000.0
        res = {"id": cid, "status": 0, "ok": False, "latency_ms": latency, "error": str(e)}

    if enforce_budget:
        with or_free.RouterStateLock():
            state = or_free.load_router_state()
            or_free.finalize_attempt_result(
                cid,
                state,
                time.time(),
                bool(res.get("ok")),
                int(res.get("status") or 0),
                float(res.get("latency_ms") or 0.0),
                str(res.get("error") or ""),
            )
            or_free.save_router_state(state, strict=True)

    return res


def _tier_for(cid: str, pricing_verified: Dict[str, Dict[str, Any]]) -> List[str]:
    meta = CAPABILITY_META.get(cid)
    # Strip any static 'long' tag first; recompute 'long' strictly from resolved context_length >= 200000
    tiers: List[str] = [t for t in ((meta or {}).get("tier") or ["general"]) if t != "long"]
    if not tiers:
        tiers = ["general"]

    # Prefer live catalog context_length over static CAPABILITY_META
    ctx: Optional[int] = None
    pv = (pricing_verified or {}).get(cid) or {}
    if pv.get("context_length") is not None:
        try:
            val = int(pv["context_length"])
            if val > 0:
                ctx = val
        except Exception:
            ctx = None
    if ctx is None and meta and meta.get("context_length") is not None:
        try:
            val = int(meta["context_length"])
            if val > 0:
                ctx = val
        except Exception:
            ctx = None

    if ctx is not None and ctx >= 200000:
        tiers.append("long")
    if not meta:
        tiers = ["general"] + (["long"] if "long" in tiers else [])
    return tiers


def build_task_pools(
    live_200: List[Dict[str, Any]],
    rate_limited_429: List[Dict[str, Any]],
    pricing_verified: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    live_ids = [x["id"] for x in live_200]
    rl_ids = [x["id"] for x in rate_limited_429]
    tiers_cache = {cid: _tier_for(cid, pricing_verified) for cid in live_ids + rl_ids}

    def sort_key(cid: str) -> Tuple[int, float]:
        meta = CAPABILITY_META.get(cid, {})
        rank = int(meta.get("rank", 999))
        lat = next((x["latency_ms"] for x in live_200 if x["id"] == cid), 9999.0)
        return (rank, lat)

    live_sorted = sorted(live_ids, key=sort_key)
    rl_sorted = sorted(rl_ids, key=lambda c: int(CAPABILITY_META.get(c, {}).get("rank", 999)))

    pools: Dict[str, Any] = {}
    descriptions = {
        "general": "Verified free pool (catalog $0 proof for OR/Kilo; curated keyless for OVH/LLM7).",
        "code": "Verified free coding-oriented pool.",
        "long": "Verified long-context only (tier contains long or context_length>=200000).",
        "fast": "Verified free low-latency candidates.",
    }
    for tname in ("general", "code", "long", "fast"):
        if tname == "long":
            tier_live = [c for c in live_sorted if "long" in tiers_cache.get(c, [])]
            tier_rl = [c for c in rl_sorted if "long" in tiers_cache.get(c, [])]
            # Do NOT dump non-long models into long
            interleave = tier_live[:10]
            fallback = list(tier_live)
            for c in tier_rl:
                if c not in fallback:
                    fallback.append(c)
            pools[tname] = {
                "description": descriptions[tname],
                "interleave_schedule": interleave,
                "fallback_chain": fallback,
            }
            continue

        tier_live = [c for c in live_sorted if tname in tiers_cache.get(c, ["general"])]
        if not tier_live:
            tier_live = list(live_sorted)
        if tname == "fast":
            tier_live = sorted(
                tier_live,
                key=lambda c: next((x["latency_ms"] for x in live_200 if x["id"] == c), 9999.0),
            )
        tier_rl = [c for c in rl_sorted if tname in tiers_cache.get(c, ["general"])]
        interleave = tier_live[:10] if tier_live else live_sorted[:8]
        fallback = list(tier_live)
        for c in tier_rl:
            if c not in fallback:
                fallback.append(c)
        for c in live_sorted:
            if c not in fallback:
                fallback.append(c)
        pools[tname] = {
            "description": descriptions[tname],
            "interleave_schedule": interleave,
            "fallback_chain": fallback,
        }
    return pools


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _compile_or_free_readonly(or_free_path: Path) -> None:
    """Compile without writing __pycache__ into (possibly read-only) skill dir."""
    src = or_free_path.read_text(encoding="utf-8")
    compile(src, str(or_free_path), "exec")


def run_security_self_tests(or_free_path: Path, registry_path: Path) -> Dict[str, Any]:
    _compile_or_free_readonly(or_free_path)
    env = os.environ.copy()
    env["OR_FREE_REGISTRY_PATH"] = str(registry_path)
    # Strip keys so cold-path tests stay keyless-safe
    for k in list(env.keys()):
        if k.endswith("_API_KEY") or k in ("OPENROUTER_API_KEY", "OR_API_KEY"):
            env[k] = ""

    # Validate registry strictly
    p_val = subprocess.run(
        [sys.executable, str(or_free_path), "--validate-registry", str(registry_path)],
        capture_output=True, text=True, timeout=30, env=env,
    )
    if p_val.returncode != 0:
        raise RuntimeError(f"Gate5 validate-registry failed rc={p_val.returncode}: {p_val.stderr[:500]}")
    try:
        val = json.loads(p_val.stdout)
    except Exception as e:
        raise RuntimeError(f"Gate5 validate-registry non-JSON: {e}; out={p_val.stdout[:300]}")
    expected_sha = _sha256_file(registry_path)
    if not val.get("valid") is True:
        raise RuntimeError(f"Gate5 valid!=True: {val}")
    if Path(val.get("registry_path", "")).resolve() != registry_path.resolve():
        raise RuntimeError(f"Gate5 registry_path mismatch: {val.get('registry_path')} vs {registry_path}")
    if val.get("sha256") != expected_sha:
        raise RuntimeError(f"Gate5 sha256 mismatch: {val.get('sha256')} vs {expected_sha}")

    cand_raw = json.loads(registry_path.read_text(encoding="utf-8"))
    cand_pools = cand_raw.get("task_pools") or {}
    out_pools = val.get("pools") or {}
    for t in ("general", "code", "long", "fast"):
        expected_fb = list((cand_pools.get(t) or {}).get("fallback_chain") or [])
        got_fb = list(out_pools.get(t) or [])
        if got_fb != expected_fb:
            raise RuntimeError(f"Gate5 pools[{t}] mismatch:\n expected={expected_fb[:5]}...\n got={got_fb[:5]}...")

    # Negative: corrupt registry must exit 4
    bad = get_app_home() / "or_free_models_registry.bad_gate5.json"
    bad.write_text("{not json", encoding="utf-8")
    env_bad = env.copy()
    env_bad["OR_FREE_REGISTRY_PATH"] = str(bad)
    p_bad = subprocess.run(
        [sys.executable, str(or_free_path), "--status"],
        capture_output=True, text=True, timeout=15, env=env_bad,
    )
    if p_bad.returncode != 4:
        raise RuntimeError(f"Gate5 negative corrupt registry expected exit 4, got {p_bad.returncode}")

    fake_secret = "sk-or-v1-" + ("a" * 48)
    p_dlp = subprocess.run(
        [sys.executable, str(or_free_path), f"Please analyze this key {fake_secret}"],
        capture_output=True, text=True, timeout=15, env=env,
    )
    p_paid = subprocess.run(
        [sys.executable, str(or_free_path), "-m", "openrouter/openai/gpt-4o", "hello"],
        capture_output=True, text=True, timeout=15, env=env,
    )
    p_kilo_paid = subprocess.run(
        [sys.executable, str(or_free_path), "-m", "kilo/anthropic/claude-3.7-sonnet", "hello"],
        capture_output=True, text=True, timeout=15, env=env,
    )
    p_fake_free = subprocess.run(
        [sys.executable, str(or_free_path), "-m", "openrouter/fake-vendor/disguised-paid:free", "hello"],
        capture_output=True, text=True, timeout=15, env=env,
    )
    p_kilo_fake = subprocess.run(
        [sys.executable, str(or_free_path), "-m", "kilo/fake-vendor/disguised:free", "hello"],
        capture_output=True, text=True, timeout=15, env=env,
    )
    if not (
        p_dlp.returncode == 3
        and p_paid.returncode == 2
        and p_kilo_paid.returncode == 2
        and p_fake_free.returncode == 2
        and p_kilo_fake.returncode == 2
    ):
        raise RuntimeError(
            f"Gate5 exit-code tests failed: dlp={p_dlp.returncode} paid={p_paid.returncode} "
            f"kilo={p_kilo_paid.returncode} fake_or={p_fake_free.returncode} fake_kilo={p_kilo_fake.returncode}"
        )
    try:
        bad.unlink()
    except Exception:
        pass
    return {
        "py_compile": "PASS",
        "validate_registry": "PASS",
        "sha256_match": "PASS",
        "pools_match": "PASS",
        "corrupt_registry_exit_4": "PASS",
        "dlp_guard_exit_3": "PASS",
        "zero_billing_exit_2": "PASS",
        "kilo_unverified_exit_2": "PASS",
        "disguised_free_suffix_exit_2": "PASS",
        "status": "PASS",
    }


def _txn_nonce() -> str:
    """Unique per-process transaction id (pid + nonce) for temp/candidate/bak names."""
    import secrets
    return f"{os.getpid()}.{secrets.token_hex(8)}"


class RegistryUpdateLock:
    """
    Cross-process exclusive lock for registry update transactions.
    Separate from RouterStateLock / request budget lock — covers candidate write,
    Gate5, backup prep, commit, and rollback only.
    Acquires all given lock paths in sorted order to avoid deadlock.
    """

    def __init__(self, lock_paths: List[Path], timeout_sec: Optional[float] = None):
        uniq: List[Path] = []
        seen: Set[str] = set()
        for p in lock_paths:
            pe = p.expanduser()
            try:
                pe.parent.mkdir(parents=True, exist_ok=True)
                resolved = pe.resolve()
            except Exception:
                resolved = pe.absolute()
            key = os.path.normcase(str(resolved))
            if key not in seen:
                seen.add(key)
                uniq.append(resolved)
        self._lock_paths = sorted(uniq, key=lambda x: os.path.normcase(str(x)))
        if timeout_sec is not None:
            self._timeout_sec = float(timeout_sec)
        else:
            try:
                self._timeout_sec = float(os.environ.get("OR_FREE_REGISTRY_LOCK_TIMEOUT_SEC", "30"))
            except Exception:
                self._timeout_sec = 30.0
        self._fhs: List[Any] = []

    def __enter__(self):
        acquired: List[Any] = []
        try:
            for path in self._lock_paths:
                path.parent.mkdir(parents=True, exist_ok=True)
                fh = open(path, "a+", encoding="utf-8")
                deadline = time.monotonic() + max(0.0, self._timeout_sec)
                last_err: Optional[Exception] = None
                while True:
                    try:
                        if os.name == "nt":
                            import msvcrt
                            fh.seek(0)
                            fh.write("0")
                            fh.flush()
                            fh.seek(0)
                            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        acquired.append(fh)
                        last_err = None
                        break
                    except Exception as e:
                        last_err = e
                        if time.monotonic() >= deadline:
                            raise RuntimeError(
                                f"Failed to acquire registry update lock on {path}: {last_err}"
                            ) from last_err
                        time.sleep(0.02)
            self._fhs = acquired
            return self
        except Exception:
            for fh in reversed(acquired):
                try:
                    if os.name == "nt":
                        import msvcrt
                        fh.seek(0)
                        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass
                try:
                    fh.close()
                except Exception:
                    pass
            raise

    def __exit__(self, exc_type, exc, tb):
        for fh in reversed(self._fhs):
            try:
                if os.name == "nt":
                    import msvcrt
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                fh.close()
            except Exception:
                pass
        self._fhs = []


def _write_all_fd(fd: int, data: bytes) -> None:
    """Loop os.write until all bytes are written; raise OSError on zero/negative progress."""
    mv = memoryview(data)
    while mv:
        n = os.write(fd, mv)
        if n <= 0:
            raise OSError("os.write made no progress (short-write abort)")
        mv = mv[n:]


def _prepare_new_backup(target: Path, nonce: str) -> Path:
    """
    Phase (a): create a NEW unique backup of an existing target.
    Never writes fixed target.bak. On failure, production is untouched.
    Writes via O_EXCL 0o600 then chmod to preserved mode (avoids umask 022 widening).
    """
    if not target.exists():
        raise FileNotFoundError(f"Cannot backup missing target {target}")
    desired_mode = target.stat().st_mode & 0o777
    bak = target.with_name(f"{target.name}.bak.{nonce}")
    bak_tmp = target.with_name(f"{target.name}.bak.tmp.{nonce}")
    fd = None
    try:
        if bak_tmp.exists() or bak_tmp.is_symlink():
            bak_tmp.unlink()
        data = target.read_bytes()
        fd = os.open(str(bak_tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        _write_all_fd(fd, data)
        try:
            os.fchmod(fd, desired_mode)
        except (AttributeError, NotImplementedError):
            os.chmod(str(bak_tmp), desired_mode)
        os.fsync(fd)
        os.close(fd)
        fd = None
        os.replace(str(bak_tmp), str(bak))
        try:
            os.chmod(str(bak), desired_mode)
        except Exception:
            pass
    except Exception:
        if fd is not None:
            try:
                os.close(fd)
            except Exception:
                pass
        try:
            if bak_tmp.exists() or bak_tmp.is_symlink():
                bak_tmp.unlink()
        except Exception:
            pass
        raise
    return bak


def _replace_production(target: Path, payload_text: str, nonce: str) -> None:
    """Phase (b): atomically replace production via unique temp + os.replace.

    Preserves existing POSIX mode bits; new user config/registry files default to 0o600.
    Temp is created O_EXCL with 0o600, then chmod'd to the desired mode before replace
    so umask 022 cannot widen a 0600 config to 0644.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        desired_mode = target.stat().st_mode & 0o777
    else:
        desired_mode = 0o600
    tmp = target.with_name(f"{target.name}.tmp.{nonce}")
    fd = None
    try:
        if tmp.exists() or tmp.is_symlink():
            tmp.unlink()
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        _write_all_fd(fd, payload_text.encode("utf-8"))
        try:
            os.fchmod(fd, desired_mode)
        except (AttributeError, NotImplementedError):
            os.chmod(str(tmp), desired_mode)
        os.fsync(fd)
        os.close(fd)
        fd = None
        os.replace(str(tmp), str(target))
    except Exception:
        if fd is not None:
            try:
                os.close(fd)
            except Exception:
                pass
        try:
            if tmp.exists() or tmp.is_symlink():
                tmp.unlink()
        except Exception:
            pass
        raise


def _prune_unique_backups(target: Path, keep: int = 3) -> None:
    """Keep at most `keep` unique `.bak.<pid>.<nonce>` files for target (by mtime desc).

    Does not touch fixed `target.bak` or `.bak.tmp.*`. Best-effort; never raises.
    Call ONLY after a successful commit — never on rollback/failure paths.
    """
    try:
        parent = target.parent
        if not parent.is_dir():
            return
        prefix = f"{target.name}.bak."
        cands: List[Path] = []
        for p in parent.iterdir():
            if not p.name.startswith(prefix):
                continue
            rest = p.name[len(prefix):]
            if not re.fullmatch(r"[0-9]+\.[0-9a-f]{16}", rest):
                continue
            if p.is_file():
                cands.append(p)
        cands.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        for old in cands[keep:]:
            try:
                old.unlink()
            except Exception:
                pass
    except Exception:
        pass


def _atomic_replace(target: Path, payload_text: str, nonce: Optional[str] = None) -> Optional[Path]:
    """
    Backward-compatible prepare+replace wrapper (returns THIS run's unique bak or None).
    Prefer phased calls inside atomic_write_registry.
    """
    if nonce is None:
        nonce = _txn_nonce()
    bak: Optional[Path] = None
    if target.exists():
        bak = _prepare_new_backup(target, nonce)
    _replace_production(target, payload_text, nonce)
    return bak


def _atomic_restore(target: Path, bak: Path, nonce: Optional[str] = None) -> None:
    if nonce is None:
        nonce = _txn_nonce()
    tmp = target.with_name(f"{target.name}.restore.tmp.{nonce}")
    try:
        shutil.copy2(bak, tmp)
        os.replace(str(tmp), str(target))
    except Exception:
        try:
            if tmp.exists() or tmp.is_symlink():
                tmp.unlink()
        except Exception:
            pass
        raise


def atomic_write_registry(
    registry_payload: dict,
    or_free_path: Path,
    output_dir: Optional[str] = None,
    sync_ob1: bool = False,
    update_bundled_seed: bool = False,
) -> Dict[str, Any]:
    """
    Write candidate → Gate5 → prepare NEW backups → replace production (locked).
    Recovery credentials are recorded ONLY for this run's successfully prepared
    backups AFTER a successful production replace. Backup-prep failure leaves
    production untouched and never restores a stale fixed .bak.
    """
    app_home = get_app_home()
    app_home.mkdir(parents=True, exist_ok=True)
    raw_targets: List[Path] = [app_home / "or_free_models_registry.json"]
    if output_dir:
        raw_targets.append(Path(output_dir).expanduser() / "or_free_models_registry.json")
    if update_bundled_seed:
        raw_targets.append(SCRIPT_DIR / "or_free_models_registry.json")

    targets: List[Path] = []
    seen_target_keys: Set[str] = set()
    for rt in raw_targets:
        resolved_t = rt.expanduser().resolve()
        t_key = os.path.normcase(str(resolved_t))
        if t_key not in seen_target_keys:
            seen_target_keys.add(t_key)
            targets.append(resolved_t)

    nonce = _txn_nonce()
    lock_paths: List[Path] = [app_home / "registry_update.lock"]
    for t in targets:
        lock_paths.append(t.with_name(t.name + ".update.lock"))

    # Pre-resolve sync-ob1 path for locking (validate later under lock)
    ob1_lock_path: Optional[Path] = None
    if sync_ob1:
        ob1_lock_path = (Path.home() / ".ob1" / "config.json").expanduser().resolve()
        lock_paths.append(ob1_lock_path.with_name(ob1_lock_path.name + ".update.lock"))

    with RegistryUpdateLock(lock_paths):
        return _atomic_write_registry_locked(
            registry_payload=registry_payload,
            or_free_path=or_free_path,
            app_home=app_home,
            targets=targets,
            nonce=nonce,
            sync_ob1=sync_ob1,
        )


def _atomic_write_registry_locked(
    registry_payload: dict,
    or_free_path: Path,
    app_home: Path,
    targets: List[Path],
    nonce: str,
    sync_ob1: bool,
) -> Dict[str, Any]:
    payload = dict(registry_payload)
    sg = dict(payload.get("security_gates") or {})
    sg["gate_5_self_test"] = {"status": "PENDING"}
    payload["security_gates"] = sg
    cand = app_home / f"or_free_models_registry.candidate.{nonce}.json"
    cand_text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    cand.write_text(cand_text, encoding="utf-8")

    backups: Dict[str, Path] = {}
    written: List[str] = []
    # Only records for targets that completed production replace in THIS run
    committed_records: List[Dict[str, Any]] = []
    prepared_backups: List[Path] = []  # unique bak files created this run (cleanup on prep-only failure)

    try:
        self_test = run_security_self_tests(or_free_path, cand)
        payload["security_gates"]["gate_5_self_test"] = self_test
        final_text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        cand.write_text(final_text, encoding="utf-8")
        env = os.environ.copy()
        env["OR_FREE_REGISTRY_PATH"] = str(cand)
        p_val = subprocess.run(
            [sys.executable, str(or_free_path), "--validate-registry", str(cand)],
            capture_output=True, text=True, timeout=30, env=env,
        )
        if p_val.returncode != 0:
            raise RuntimeError(f"Final candidate validate failed: {p_val.stderr[:400]}")

        ob1_plan: Optional[Tuple[Path, bool, str]] = None
        if sync_ob1:
            ob1 = (Path.home() / ".ob1" / "config.json").expanduser().resolve()
            ob1_existed = ob1.exists()
            if ob1_existed:
                try:
                    loaded = json.loads(ob1.read_text(encoding="utf-8"))
                except Exception as e:
                    raise RuntimeError(
                        f"Refusing --sync-ob1: existing ~/.ob1/config.json is unreadable or invalid JSON ({e})"
                    )
                if not isinstance(loaded, dict):
                    raise RuntimeError(
                        "Refusing --sync-ob1: existing ~/.ob1/config.json is unreadable, invalid JSON, or not a JSON object"
                    )
                existing = dict(loaded)
            else:
                existing = {}
            existing["or_free_task_pools"] = payload.get("task_pools", {})
            ob1_text = json.dumps(existing, indent=2, ensure_ascii=False) + "\n"
            ob1_plan = (ob1, ob1_existed, ob1_text)

        # ---- PHASE A: prepare NEW backups for all existing targets (no production mutate) ----
        prep_list: List[Tuple[Path, bool, Optional[Path], Optional[str]]] = []
        # each: (path, existed_before, prepared_bak, payload_or_None for ob1 override)
        try:
            for t in targets:
                existed_before = t.exists()
                prepared_bak: Optional[Path] = None
                if existed_before:
                    prepared_bak = _prepare_new_backup(t, nonce)
                    prepared_backups.append(prepared_bak)
                prep_list.append((t, existed_before, prepared_bak, None))
            if ob1_plan is not None:
                ob1, ob1_existed, ob1_text = ob1_plan
                prepared_bak = None
                if ob1_existed:
                    prepared_bak = _prepare_new_backup(ob1, nonce)
                    prepared_backups.append(prepared_bak)
                prep_list.append((ob1, ob1_existed, prepared_bak, ob1_text))
        except Exception:
            # Backup prep failed BEFORE any replace: leave production untouched.
            # Do NOT restore stale fixed .bak. Clean up unique bak files we created.
            for pb in prepared_backups:
                try:
                    if pb.exists() or pb.is_symlink():
                        pb.unlink()
                except Exception:
                    pass
            raise

        # ---- PHASE B: replace production; record recovery ONLY after successful replace ----
        for path, existed_before, prepared_bak, override_text in prep_list:
            text = final_text if override_text is None else override_text
            _replace_production(path, text, nonce)
            rec: Dict[str, Any] = {
                "path": path,
                "existed_before": existed_before,
                "backup_path": prepared_bak,  # THIS run's unique bak only (or None)
                "replaced": True,
            }
            committed_records.append(rec)
            if prepared_bak is not None:
                backups[str(path)] = prepared_bak
                # Best-effort refresh of stable .bak AFTER successful replace (not used for rollback)
                try:
                    stable_bak = path.with_suffix(path.suffix + ".bak")
                    shutil.copy2(prepared_bak, stable_bak)
                except Exception:
                    pass
            if override_text is None:
                written.append(str(path))
            else:
                written.append(str(path) + " (merged or_free_task_pools only)")

        # Success-only: prune unique .bak.<pid>.<nonce> (keep last 3 per target)
        for path, existed_before, _prepared_bak, _override_text in prep_list:
            if existed_before:
                _prune_unique_backups(path, keep=3)

        try:
            cand.unlink()
        except Exception:
            pass
        return {"written": written, "self_test": self_test, "backups": {k: str(v) for k, v in backups.items()}}
    except Exception as e:
        try:
            if cand.exists():
                cand.unlink()
        except Exception:
            pass
        restore_errors: List[str] = []
        # Rollback ONLY targets confirmed replaced in THIS run.
        for rec in reversed(committed_records):
            r_path: Path = rec["path"]
            r_existed: bool = rec["existed_before"]
            r_bak: Optional[Path] = rec["backup_path"]
            try:
                if r_existed:
                    if r_bak is None or not r_bak.is_file():
                        raise RuntimeError(f"Missing this-run backup file {r_bak} for replaced target {r_path}")
                    _atomic_restore(r_path, r_bak, nonce=nonce)
                else:
                    if r_path.exists() or r_path.is_symlink():
                        r_path.unlink()
            except Exception as re_err:
                msg = f"Rollback failed for {r_path}: {re_err}"
                print(f"[ROLLBACK ERROR] {msg}", file=sys.stderr)
                restore_errors.append(msg)
        # If we prepared backups but never replaced (committed_records empty), do nothing to production.
        if restore_errors:
            raise RuntimeError(
                f"Commit failed ({e}); rollback also encountered {len(restore_errors)} error(s): {'; '.join(restore_errors)}"
            ) from e
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Daily safe free-model scanner for or-free")
    parser.add_argument("--apply", action="store_true", help="Atomically write updated or_free_models_registry.json (with rollback)")
    parser.add_argument("--output-dir", default=None, help="Also write registry copy to this directory")
    parser.add_argument("--sync-ob1", action="store_true", help="Non-destructively merge task_pools into ~/.ob1/config.json as or_free_task_pools")
    parser.add_argument("--update-bundled-seed", action="store_true", help="Also update scripts/or_free_models_registry.json beside this scanner (maintainer only)")
    parser.add_argument("--allow-unverified-model", action="store_true", help="Opt-in (still excludes NVIDIA/RouteOpen from default free pool)")
    parser.add_argument("--min-live", type=int, default=1, help="Minimum HTTP-200 verified probes required before --apply (default 1)")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON summary to stdout (human progress logs go to stderr)")
    args = parser.parse_args()
    log_out = sys.stderr if args.json else sys.stdout

    or_free_path = SCRIPT_DIR / "or_free.py"
    if not or_free_path.is_file():
        print(f"[ERROR] Sibling or_free.py not found at {or_free_path}", file=sys.stderr)
        sys.exit(1)

    or_key = load_provider_key("openrouter")
    ro_key = load_provider_key("routeopen")

    print(f"=== or-free Daily Safe Scanner ({now_str()}) ===", file=log_out)
    print(f"App home           : {get_app_home()}", file=log_out)
    print(f"Sibling or_free.py : {or_free_path}", file=log_out)

    candidates, skipped_info, pricing_verified, curated_keyless = discover_catalog_models(
        allow_unverified=args.allow_unverified_model
    )
    # Keep pricing_verified only for OR/Kilo discovered; curated separate
    probe_results: List[Dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(probe_single_candidate, cid, or_key, ro_key): cid for cid in candidates}
        for f in concurrent.futures.as_completed(futs):
            probe_results.append(f.result())

    live_200 = sorted(
        [r for r in probe_results if r.get("status") == 200 and r.get("ok")],
        key=lambda x: (CAPABILITY_META.get(x["id"], {}).get("rank", 999), x["latency_ms"]),
    )
    skipped_budget = sorted(
        [r for r in probe_results if r.get("probe_state") == "skipped_budget"],
        key=lambda x: CAPABILITY_META.get(x["id"], {}).get("rank", 999),
    )
    rl_429 = sorted(
        [
            r for r in probe_results
            if r.get("status") == 429 and r.get("probe_state") != "skipped_budget"
        ],
        key=lambda x: CAPABILITY_META.get(x["id"], {}).get("rank", 999),
    )
    dead_other = [
        r for r in probe_results
        if not (r.get("status") == 200 and r.get("ok"))
        and r.get("status") != 429
        and r.get("probe_state") != "skipped_budget"
    ]

    # Restrict pricing_verified / curated to models that were admitted (live or 429 standby that passed Gate2)
    admitted_ids = {r["id"] for r in live_200} | {r["id"] for r in rl_429}
    # Keep catalog proofs for all Gate2-passed models that were discovered (even if probe failed),
    # but only publish proofs for live+429 in registry for runtime routing safety.
    pricing_out = {k: v for k, v in pricing_verified.items() if k in admitted_ids or k in {r["id"] for r in probe_results if r.get("id") in pricing_verified}}
    # Prefer: all catalog-zero models that were in discover list AND (live or 429)
    pricing_out = {k: v for k, v in pricing_verified.items() if k in admitted_ids}
    # Also keep proofs for catalog-zero that weren't probed-ok? Spec says including both 200 live and 429 standby that passed Gate2.
    # That's admitted_ids. Good.
    curated_out = {k: v for k, v in curated_keyless.items() if k in admitted_ids or k in KEYLESS_ALLOWLIST}
    # Only include curated that are live or 429
    curated_out = {k: v for k, v in curated_keyless.items() if k in admitted_ids}

    if args.apply and len(live_200) < args.min_live:
        print(f"[ERROR] Only {len(live_200)} live probes (min {args.min_live}); refusing --apply", file=sys.stderr)
        sys.exit(1)

    task_pools = build_task_pools(live_200, rl_429, pricing_verified)

    registry_payload: Dict[str, Any] = {
        "schema_version": "2026.10.28-v5",
        "updated_at": now_str(),
        "updated_epoch": int(time.time()),
        "pricing_verified_models": pricing_out,
        "curated_keyless_allowlist_models": curated_out,
        "verified_free_models": sorted(list(pricing_out.keys()) + list(curated_out.keys())),
        "security_gates": {
            "gate_1_https_whitelist": "ENFORCED",
            "gate_2_zero_billing_lock": (
                "ENFORCED (OpenRouter/Kilo require live catalog pricing.prompt==0 & pricing.completion==0 proof; "
                "OVH/LLM7 use curated keyless allowlist — vendor catalogs use rate-card stubs; no API key/billing attached; "
                "NVIDIA/RouteOpen default-off)"
            ),
            "gate_3_anti_poisoning": "ENFORCED (<7B tiny models and safety/embedding classifiers excluded)",
            "gate_4_live_probe": f"PASS ({len(live_200)} verified content==42, {len(rl_429)} HTTP 429 standby, {len(skipped_budget)} local budget skip, {len(dead_other)} pruned)",
            "gate_5_self_test": {"status": "PENDING"},
        },
        "summary": {
            "total_probed": len(probe_results),
            "live_200": len(live_200),
            "standby_429": len(rl_429),
            "skipped_budget": len(skipped_budget),
            "excluded": len(skipped_info) + len(dead_other) + len(skipped_budget),
        },
        "live_200_models": [
            {
                "id": r["id"],
                "latency_ms": round(r["latency_ms"], 1),
                "rank": CAPABILITY_META.get(r["id"], {}).get("rank", 999),
                "note": CAPABILITY_META.get(r["id"], {}).get("note", "Verified Free Model"),
            }
            for r in live_200
        ],
        "fallback_429_models": [
            {
                "id": r["id"],
                "rank": CAPABILITY_META.get(r["id"], {}).get("rank", 999),
                "note": CAPABILITY_META.get(r["id"], {}).get("note", "Catalog Active (429 Standby)"),
            }
            for r in rl_429
        ],
        "demoted_or_excluded": skipped_info[:200],
        "task_pools": task_pools,
    }

    apply_info: Optional[Dict[str, Any]] = None
    if args.apply:
        apply_info = atomic_write_registry(
            registry_payload,
            or_free_path=or_free_path,
            output_dir=args.output_dir,
            sync_ob1=args.sync_ob1,
            update_bundled_seed=args.update_bundled_seed,
        )
        print(f"Gate 5 Self-Tests  : {json.dumps(apply_info['self_test'], ensure_ascii=False)}", file=log_out)
        print(f"Written            : {apply_info['written']}", file=log_out)
    else:
        print("Dry-run (pass --apply to write registry with atomic rollback)", file=log_out)

    print(
        f"Verified live      : {len(live_200)} | 429 standby: {len(rl_429)} | "
        f"budget skip: {len(skipped_budget)} | Excluded/Demoted: {len(skipped_info)+len(dead_other)}",
        file=log_out,
    )
    for r in live_200:
        note = CAPABILITY_META.get(r["id"], {}).get("note", "Verified Free Model")
        print(f"  [OK ] {r['id']:55s} ({r['latency_ms']:6.1f}ms) | {note}", file=log_out)
    for r in rl_429:
        note = CAPABILITY_META.get(r["id"], {}).get("note", "429 Standby")
        print(f"  [429] {r['id']:55s} (standby) | {note}", file=log_out)
    for r in skipped_budget:
        print(f"  [SKIP] {r['id']:55s} (local budget) | {r.get('error', '')}", file=log_out)

    md_lines = [
        f"# or-free Free Model Scan — {now_str()}",
        "",
        f"- **Probed**: `{len(probe_results)}` | **Verified live (content==42)**: `{len(live_200)}` | **429 standby**: `{len(rl_429)}` | **local budget skip**: `{len(skipped_budget)}`",
        f"- **Pricing proofs (OR/Kilo catalog $0)**: `{len(pricing_out)}`",
        f"- **Curated keyless (OVH/LLM7)**: `{len(curated_out)}`",
        "",
        "## Honesty note",
        "OpenRouter & Kilo models require live catalog `pricing.prompt == 0` & `pricing.completion == 0` proof. "
        "OVH & LLM7 keyless models use a strict curated keyless allowlist (no API key or billing account attached). "
        "We never claim OVH/LLM7 pass catalog `$0/$0`.",
        "",
    ]
    report_path = get_app_home() / "free_model_scan_latest.md"
    report_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    if args.output_dir:
        Path(args.output_dir).mkdir(parents=True, exist_ok=True)
        (Path(args.output_dir) / "free_model_scan_latest.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    print(f"Report             : {report_path}", file=log_out)

    if args.json:
        json_out = {
            "schema_version": registry_payload["schema_version"],
            "updated_at": registry_payload["updated_at"],
            "updated_epoch": registry_payload["updated_epoch"],
            "summary": registry_payload["summary"],
            "applied": bool(args.apply),
            "written": apply_info["written"] if apply_info else [],
            "self_test": apply_info["self_test"] if apply_info else None,
            "live_200_models": registry_payload["live_200_models"],
            "fallback_429_models": registry_payload["fallback_429_models"],
            "task_pools": registry_payload["task_pools"],
        }
        print(json.dumps(json_out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
