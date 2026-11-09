#!/usr/bin/env python3
"""Workspace-agnostic security, provenance, Kortex token, and anti-slop kernel (`core_hooks.py`).

Shared across all 5 local Antigravity workspaces (`claire-architect`, `elmer`, `chad`, `andy`, `grant`).
Contains zero SMS, shared calendar, or `[Claire Debug Mode]` logic; all orchestrator-specific
policies live exclusively in `claire_hooks.py`.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import fcntl
import glob
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from typing import Any, Callable
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo(os.environ.get("CLAIRE_TIMEZONE", "UTC"))
ALL_WORKSPACE_NAMES = ("claire-architect", "elmer", "chad", "andy", "grant")
DANGLING_REPLY_TTL_SECONDS = 24 * 3600
MAX_EXTERNAL_LOOKUPS = 50
MAX_SECURITY_INCIDENTS = 25
UNTRUSTED_INBOX_ID = os.environ.get("ANTIGRAVITY_UNTRUSTED_INBOX_ID", "00_antigravity_inbox_folder_id")
GENESIS_HASH = "0" * 64
SUBSTANTIVE_DATE_STAMP_DELTA_BYTES = 16
MAX_AUDIT_LOG_BYTES = int(os.environ.get("CLAIRE_MAX_AUDIT_BYTES", str(10 * 1024 * 1024)))
MAX_BAK_SEGMENTS = int(os.environ.get("CLAIRE_MAX_BAK_SEGMENTS", "5"))

DEFAULT_KORTEX_ACCOUNT = os.environ.get("KORTEX_DEFAULT_ACCOUNT", "operator@example.com")
ACCOUNTLESS_KORTEX_TOOLS = {"get_status", "list_accounts", "rate_kortex", "get_job"}
KORTEX_MUTATION_TOOLS = {
    "add_text_source",
    "add_url_source",
    "add_google_doc",
    "sync_google_docs",
    "create_notebook",
    "rename_notebook",
    "rename_source",
    "create_notebooklm_note",
    "update_notebooklm_note",
    "set_source_note",
    "set_source_highlight",
    "save_chat_to_note",
    "convert_note_to_source",
    "bulk",
    "batch_to_vault",
    "import_pipeline",
}

HIDDEN_UNICODE_RE = re.compile(
    r"[\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff\U000e0000-\U000e007f]"
)

STORED_INJECTION_RE = re.compile(
    r"(?:<\s*/?\s*(?:system|user_rules|claude_instruction|antigravity_instruction)\b"
    r"|<\s*RULE\s*\["
    r"|ignore\s+(?:all\s+)?(?:previous|prior)\s+instructions"
    r"|disregard\s+(?:all\s+)?(?:previous|prior)\s+instructions"
    r"|system\s+override\s*:)",
    re.IGNORECASE,
)

EXFIL_AND_SLOPSQUAT_CMD_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])(curl|wget|nc|ncat|netcat|scp|sftp|ssh|rsync"
    r"|git\s+push|gh\s+(?:pr|issue|repo|gist|api)"
    r"|python3?\s+-m\s+pip\s+install|pip3?\s+install|uv\s+pip\s+install|poetry\s+add"
    r"|npm\s+(?:install|i)\b|npx\b|pnpm\s+add|yarn\s+add|brew\s+install|cargo\s+install|go\s+install"
    r"|urllib\.request|http\.client|socket\.create_connection|requests\.(?:get|post|put|request)|httpx\.|fetch\s*\()",
    re.IGNORECASE,
)

EXOGENOUS_LOOKUP_TOOLS = {
    "mcp_gemini-api_gemini-api-docs_gemini_search_docs",
    "mcp_gemini-api_gemini-api-docs_gemini_get_doc",
    "search_web",
    "read_url_content",
    "search_marketplace",
}

TIER_1_SPEC_DOMAINS = (
    "docs.python.org",
    "peps.python.org",
    "packaging.python.org",
    "ai.google.dev",
    "cloud.google.com",
    "firebase.google.com",
    "developer.android.com",
    "developer.chrome.com",
    "web.dev",
    "w3.org",
    "rfc-editor.org",
    "ietf.org",
    "modelcontextprotocol.io",
    "developer.mozilla.org",
    "sqlite.org",
    "postgresql.org",
    "git-scm.com",
    "typescriptlang.org",
    "nodejs.org",
    "rust-lang.org",
    "go.dev",
)

TIER_2_PRACTITIONER_DOMAINS = (
    "github.com",
    "raw.githubusercontent.com",
    "arxiv.org",
    "dl.acm.org",
    "ieee.org",
    "usenix.org",
    "simonwillison.net",
    "eugeneyan.com",
    "hamel.dev",
    "shreyashankar.com",
    "lilianweng.github.io",
    "huyenchip.com",
    "anthropic.com",
    "openai.com",
    "deepmind.google",
    "stanford.edu",
    "berkeley.edu",
    "mit.edu",
    "llmstxt.org",
    "context7.com",
    "swebench.com",
)

VAULT_SOURCE_SPECS: dict[str, dict[str, Any]] = {
    "00000000-0000-4000-8000-000000000100": {"label": "Orchestrator_Architecture_Vault", "volatility": "CONTAINER", "ttl_days": None, "parent": None, "owner": "claire-architect"},
    "00000000-0000-4000-8000-000000000101": {"label": "01_Operator_And_Counterweight_Spec.md", "volatility": "STRUCTURAL_IDENTITY", "ttl_days": 90, "parent": "00000000-0000-4000-8000-000000000100", "owner": "claire-architect"},
    "00000000-0000-4000-8000-000000000102": {"label": "02_Active_Operations_And_Sprint_Protocols.md", "volatility": "OPERATIONAL_SPRINT", "ttl_days": 7, "parent": "00000000-0000-4000-8000-000000000100", "owner": "claire-architect"},
    "00000000-0000-4000-8000-000000000103": {"label": "03_Shared_Calendar_And_Comms_Governance.md", "volatility": "STRUCTURAL_GOVERNANCE", "ttl_days": 90, "parent": "00000000-0000-4000-8000-000000000100", "owner": "claire-architect"},
    "00000000-0000-4000-8000-000000000104": {"label": "04_Chronological_Changelog_Directives.md", "volatility": "ARCHITECTURAL_CHANGELOG", "ttl_days": 30, "parent": "00000000-0000-4000-8000-000000000100", "owner": "claire-architect"},
    "00000000-0000-4000-8000-000000000200": {"label": "Media_Storyboard_Vault", "volatility": "DEFERRED_DORMANT", "ttl_days": None, "parent": None, "owner": "claire-architect"},
    "00000000-0000-4000-8000-000000000201": {"label": "00_STATE_Media_Production_Spec.md", "volatility": "DEFERRED_DORMANT", "ttl_days": None, "parent": "00000000-0000-4000-8000-000000000200", "owner": "claire-architect"},
    "00000000-0000-4000-8000-000000000300": {"label": "Biometrics_Vault", "volatility": "DOMAIN_DELEGATED", "ttl_days": None, "parent": None, "owner": "chad"},
    "00000000-0000-4000-8000-000000000400": {"label": "Campaign_Primary_Vault", "volatility": "DOMAIN_DELEGATED", "ttl_days": None, "parent": None, "owner": "elmer"},
    "00000000-0000-4000-8000-000000000401": {"label": "Campaign_SciFi_Vault", "volatility": "DOMAIN_DELEGATED", "ttl_days": None, "parent": None, "owner": "elmer"},
    "00000000-0000-4000-8000-000000000402": {"label": "Tabletop_Rules_Vault", "volatility": "DOMAIN_DELEGATED", "ttl_days": None, "parent": None, "owner": "elmer"},
    "00000000-0000-4000-8000-000000000500": {"label": "Quant_Lab_Vault", "volatility": "DOMAIN_DELEGATED", "ttl_days": None, "parent": None, "owner": "grant"},
}

KORTEX_UUID_LABELS: dict[str, str] = {uid: spec["label"] for uid, spec in VAULT_SOURCE_SPECS.items()}
ECOSYSTEM_REGISTRY_UUIDS: frozenset[str] = frozenset(uid.lower() for uid in VAULT_SOURCE_SPECS.keys())


def register_vault_source_specs(specs: dict[str, dict[str, Any]]) -> None:
    """Register or update workspace-owned NotebookLM Vault UUID specifications."""
    for uid, spec in specs.items():
        VAULT_SOURCE_SPECS[uid] = dict(spec)
        KORTEX_UUID_LABELS[uid] = str(spec.get("label") or uid)


def register_vault_manifest_file(manifest_path: str, owner: str) -> dict[str, Any]:
    """Load a domain state/vault_manifest.json and register its notebooks, sources, and studio notes."""
    if not os.path.isfile(manifest_path):
        return {}
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except Exception:
        return {}
    if not isinstance(manifest, dict):
        return {}

    specs: dict[str, dict[str, Any]] = {}
    top_nb = manifest.get("notebook_id")
    if isinstance(top_nb, str) and top_nb:
        for s_label, s_uid in (manifest.get("canonical_sources") or {}).items():
            if isinstance(s_uid, str) and s_uid:
                specs[s_uid] = {"label": s_label, "volatility": "DOMAIN_CANONICAL", "ttl_days": None, "parent": top_nb, "owner": owner}
        for n_label, n_uid in (manifest.get("live_studio_notes") or {}).items():
            if isinstance(n_uid, str) and n_uid:
                specs[n_uid] = {"label": n_label, "volatility": "DOMAIN_HOT_NOTE", "ttl_days": None, "parent": top_nb, "owner": owner}

    for sec_key in ("campaign_primary", "campaign_secondary", "archival_rules"):
        sec = manifest.get(sec_key)
        if not isinstance(sec, dict):
            continue
        nb_uid = str(sec.get("notebook_id") or "")
        for s_label, s_uid in (sec.get("sources") or {}).items():
            if isinstance(s_uid, str) and s_uid:
                specs[s_uid] = {"label": f"{sec_key}:{s_label}", "volatility": "DOMAIN_SOURCE", "ttl_days": None, "parent": nb_uid or None, "owner": owner}
        for n_label, n_uid in (sec.get("notes") or {}).items():
            if isinstance(n_uid, str) and n_uid and n_uid not in specs:
                specs[n_uid] = {"label": f"{sec_key}:{n_label}", "volatility": "DOMAIN_HOT_NOTE", "ttl_days": None, "parent": nb_uid or None, "owner": owner}

    if specs:
        register_vault_source_specs(specs)
    return manifest


def validate_domain_vault_manifests(ecosystem_root: str) -> list[str]:
    """Verify domain vault_manifest.json internal bucket/source integrity across workspaces."""
    issues: list[str] = []
    elmer_m_path = os.path.join(ecosystem_root, "elmer", "state", "vault_manifest.json")
    if os.path.isfile(elmer_m_path):
        try:
            with open(elmer_m_path, "r", encoding="utf-8") as ef:
                em = json.load(ef)
            for sec_key in ("campaign_primary", "campaign_secondary"):
                sec = em.get(sec_key)
                if not isinstance(sec, dict):
                    continue
                known_src_uuids = set((sec.get("sources") or {}).values())
                for b_name, b_list in (sec.get("buckets") or {}).items():
                    if isinstance(b_list, list):
                        for b_uid in b_list:
                            if b_uid not in known_src_uuids:
                                issues.append(f"elmer/state/vault_manifest.json:{sec_key}.{b_name} references unmapped UUID {b_uid[:8]}")
        except Exception as exc:
            issues.append(f"elmer/state/vault_manifest.json parse error: {exc}")
    chad_m_path = os.path.join(ecosystem_root, "chad", "state", "vault_manifest.json")
    if os.path.isfile(chad_m_path):
        try:
            with open(chad_m_path, "r", encoding="utf-8") as cf:
                cm = json.load(cf)
            if not (cm.get("canonical_sources") and cm.get("live_studio_notes")):
                issues.append("chad/state/vault_manifest.json missing canonical_sources or live_studio_notes")
        except Exception as exc:
            issues.append(f"chad/state/vault_manifest.json parse error: {exc}")
    return issues


WORKSPACE_PROMPT_FILES: dict[str, str] = {
    "claire-architect": "SYSTEM_PROMPT.md",
    "elmer": "ELMER_PROMPT.md",
    "chad": "CHAD_PROMPT.md",
    "andy": "ANDY_PROMPT.md",
    "grant": "GRANT_PROMPT.md",
}

REQUIRED_PERSONA_MARKERS: tuple[str, ...] = (
    "**Operational Acronym:**",
    "**Role:**",
    "**Cognitive Stack:**",
    "**Anti-Roleplay Directive:**",
    "**Core Architecture Lock",
    "**Council Subagent Delegation Clause (Veil of Ignorance):**",
    "Observed Reality > Typology Heuristic",
)

VALID_SEAT_IDENTITY_RE = re.compile(
    r"^# COUNCIL SEAT IDENTITY:\s+([A-Z]+-(?:INTJ|INTP|ENTJ|ENTP|INFJ|INFP|ENFJ|ENFP|ISTJ|ISFJ|ESTJ|ESFJ|ISTP|ISFP|ESTP|ESFP))\b"
)


def validate_persona_and_panel_congruence(ecosystem_root: str) -> list[str]:
    """Verify cross-workspace persona header schema, GEMINI.md parity, and <scope>-<mbti> critic panel congruence."""
    issues: list[str] = []
    claire_dir = os.path.join(ecosystem_root, "claire-architect")
    if not os.path.isdir(claire_dir) and os.path.isdir(os.path.join(ecosystem_root, ".agents")):
        claire_dir = ecosystem_root
    canonical_files = (
        ".agents/skills/iterate/SKILL.md",
        ".agents/skills/iterate/references/critic_panels.md",
    )
    canonical_shas: dict[str, str] = {}
    for rel_c in canonical_files:
        cp = os.path.join(claire_dir, rel_c)
        if os.path.isfile(cp):
            try:
                with open(cp, "rb") as f:
                    canonical_shas[rel_c] = hashlib.sha256(f.read()).hexdigest()
            except OSError:
                pass

    for spec_rel in (
        ".agents/skills/council/references/personas.md",
        ".agents/skills/iterate/references/critic_panels.md",
    ):
        sp_path = os.path.join(claire_dir, spec_rel)
        if not os.path.isfile(sp_path):
            continue
        try:
            with open(sp_path, "r", encoding="utf-8") as sf:
                txt = sf.read()
            if re.search(r"\btenth[- ]man\b", txt, re.IGNORECASE):
                issues.append(f"claire-architect/{spec_rel} contains deprecated 'Tenth Man' label")
            for ln in txt.splitlines():
                if ln.startswith("# COUNCIL SEAT IDENTITY:"):
                    if not VALID_SEAT_IDENTITY_RE.match(ln):
                        issues.append(f"claire-architect/{spec_rel} invalid non-MBTI seat header: {ln}")
        except OSError:
            pass

    for ws, prompt_name in WORKSPACE_PROMPT_FILES.items():
        ws_dir = os.path.join(ecosystem_root, ws)
        p_path = os.path.join(ws_dir, prompt_name)
        g_path = os.path.join(ws_dir, "GEMINI.md")
        if os.path.isfile(p_path):
            try:
                with open(p_path, "rb") as pf:
                    p_bytes = pf.read()
                p_txt = p_bytes.decode("utf-8", errors="replace")
                for marker in REQUIRED_PERSONA_MARKERS:
                    if marker not in p_txt:
                        issues.append(f"{ws}/{prompt_name} missing required persona marker: {marker}")
                if os.path.isfile(g_path):
                    with open(g_path, "rb") as gf:
                        g_bytes = gf.read()
                    if hashlib.sha256(p_bytes).hexdigest() != hashlib.sha256(g_bytes).hexdigest():
                        issues.append(f"{ws}/{prompt_name} and {ws}/GEMINI.md SHA-256 mismatch")
            except OSError:
                pass
        if ws != "claire-architect":
            for rel_c, c_sha in canonical_shas.items():
                ws_c = os.path.join(ws_dir, rel_c)
                if os.path.isfile(ws_c):
                    try:
                        with open(ws_c, "rb") as wf:
                            w_sha = hashlib.sha256(wf.read()).hexdigest()
                        if w_sha != c_sha:
                            issues.append(f"{ws}/{rel_c} drifted from claire-architect/{rel_c}")
                    except OSError:
                        pass

    ws_specs_dir = os.path.join(claire_dir, "workspaces")
    if os.path.isdir(ws_specs_dir):
        for fn in sorted(os.listdir(ws_specs_dir)):
            if not fn.endswith(".md"):
                continue
            wp = os.path.join(ws_specs_dir, fn)
            try:
                with open(wp, "r", encoding="utf-8") as wf:
                    w_txt = wf.read()
                for marker in REQUIRED_PERSONA_MARKERS:
                    if marker not in w_txt:
                        issues.append(f"workspaces/{fn} missing required persona marker: {marker}")
            except OSError:
                pass

    return issues


def normalize_kortex_bulk_ops(mcp_args: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Normalize Kortex bulk arguments across upstream {'tool': ..., 'calls': [...]} and {'operations': [...]} schemas."""
    if not isinstance(mcp_args, dict):
        return []
    top_tool = str(mcp_args.get("tool") or mcp_args.get("name") or "").strip()
    out: list[tuple[str, dict[str, Any]]] = []
    calls = mcp_args.get("calls")
    if isinstance(calls, list):
        for c in calls:
            if isinstance(c, dict):
                op_t = str(c.get("tool") or c.get("name") or top_tool).strip()
                op_a = c.get("args") or c.get("arguments") or c
                if isinstance(op_a, dict):
                    out.append((op_t, op_a))
    ops = mcp_args.get("operations")
    if isinstance(ops, list):
        for op in ops:
            if isinstance(op, dict):
                op_t = str(op.get("tool") or op.get("name") or top_tool).strip()
                op_a = op.get("args") or op.get("arguments") or op
                if isinstance(op_a, dict):
                    out.append((op_t, op_a))
    return out


WORKSPACE_STATE_FILE_SPECS: dict[str, list[tuple[str, int, int, int | None]]] = {
    "claire-architect": [
        ("active_sprint.md", 100, 8000, 14),
        ("ecosystem_registry.md", 100, 8000, None),
        ("routing_matrix.md", 100, 8000, None),
        ("archive_decisions.md", 150, 16000, None),
    ],
    "elmer": [
        ("campaign_primary_summary.md", 100, 8000, 7),
        ("campaign_primary_roster.md", 100, 8000, 7),
        ("campaign_secondary_summary.md", 100, 8000, 7),
    ],
    "chad": [
        ("active_protocol_and_labs.md", 100, 8000, None),
        ("training_program_state.md", 100, 8000, None),
    ],
    "andy": [
        ("style_and_catalog_dna.md", 100, 8000, None),
        ("active_setlist.md", 100, 8000, None),
    ],
    "grant": [
        ("strategy_lab_and_models.md", 100, 8000, 3),
        ("paper_portfolio_and_ledger.md", 100, 8000, 3),
    ],
}


def get_workspace_state_files_to_audit(
    workspace_root: str,
) -> list[tuple[str, str, int, int, int | None]]:
    """Return the (label, abs_path, max_lines, max_bytes, max_stale_days) audit list for any workspace."""
    ws_name = os.path.basename(workspace_root.rstrip("/"))
    specs = WORKSPACE_STATE_FILE_SPECS.get(ws_name, WORKSPACE_STATE_FILE_SPECS["claire-architect"])
    return [
        (label, os.path.join(workspace_root, "state", label), max_l, max_b, max_stale)
        for label, max_l, max_b, max_stale in specs
    ]

BANNED_SLOP_TERMS_RE = re.compile(
    r"\b(delve|delving|tapestry|testament|pivotal|crucial|underscore|underscores|"
    r"foster|fostering|streamline|seamless|paradigm|synergy|holistic|multifaceted|"
    r"overarching|flywheel|north star|connective tissue|load-bearing|sieve|unpack|"
    r"double-click|move the needle|symphonic|battle-tested)\b",
    re.IGNORECASE,
)

CONTRAST_FRAME_RE = re.compile(
    r"\b(?:it's not\s+[^.,;\n]+,\s*it's|isn't just about\s+[^.,;\n]+;\s*it's about|"
    r"less like\s+[^.,;\n]+,\s*and more like)\b",
    re.IGNORECASE,
)

PSEUDO_WEIGHT_RE = re.compile(r"\[WEIGHT:\s*[^\]]+\]", re.IGNORECASE)

SECRET_FLAG_REDACT_RE = re.compile(
    r"((?:--body|--text|--message|--note|--description|--summary|--title|--location|--comment|"
    r"--prompt|--attendees|--decision|--consequences|-m|"
    r"token|api_key|apikey|password|secret)\s*(?:=|\s+)\s*)(?:'[^']*'|\"[^\"]*\"|\S+)",
    re.IGNORECASE,
)
QUICK_ADD_REDACT_RE = re.compile(
    r"(\bgcalendar\b[^\n;|&]*?\bquick-add\b(?:\s+--?[a-zA-Z0-9_-]+(?:\s*(?:=|\s+)\s*(?:'[^']*'|\"[^\"]*\"|\S+))?)*\s+)(?:'[^']*'|\"[^\"]*\"|[^;|&>\n]+)",
    re.IGNORECASE,
)
EMAIL_REDACT_RE = re.compile(r"\b[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+\b")
PHONE_REDACT_RE = re.compile(r"(?<!\w)(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}(?!\w)")
ISO_DATETIME_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2})?)\b")

BENIGN_REDIRECT_RE = re.compile(r"(?:\d*>\s*/dev/null|\d+>&\d+|>&\d+)")
COMPOUND_SHELL_OP_RE = re.compile(r"(?:;|&&|\|\||\|(?!\|)|`|\$\(|\n)")
SELF_OBSERVER_ANCHORED_RE = re.compile(
    r"^\s*(?:python3?\s+)?(?:\S*/)?(?:audit_log\.py\s+(?:query|summary|verify|changelog|record-adr|epistemic|tokens)|"
    r"iterate_stage\.py\s+(?:status|diff))(?:\s+[^;&|`$><\n]*)?\s*$"
)
UUID_EXTRACT_RE = re.compile(
    r"\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b",
    re.IGNORECASE,
)
GCAL_MUTATE_RE = re.compile(
    r"\bgcalendar\b[^\n;|&]*?\b(create|add|insert|update|patch|delete|remove|move|quick-add|rsvp|import|focus-time|out-of-office)\b",
    re.IGNORECASE,
)
GDRIVE_MUTATE_RE = re.compile(
    r"\bgdrive\b[^\n;|&]*?\b(upload|create|update|move|rename|copy|delete|rm|mkdir|share|permissions\s+(?:add|create|update|delete|remove)|comments\s+(?:add|create|delete|resolve))\b",
    re.IGNORECASE,
)


def _resolve_default_workspace_root(caller_file: str | None = None) -> str:
    env_root = os.environ.get("CLAIRE_WORKSPACE_ROOT")
    if env_root:
        return os.path.abspath(env_root)
    candidates = [
        caller_file,
        sys.argv[0] if sys.argv and sys.argv[0] and sys.argv[0] != "-c" else None,
        __file__,
    ]
    for cand in candidates:
        if not cand:
            continue
        for p_obj in (Path(os.path.abspath(cand)), Path(cand).resolve()):
            if (
                len(p_obj.parents) >= 3
                and p_obj.parent.name == "scripts"
                and p_obj.parents[1].name == ".agents"
            ):
                return str(p_obj.parents[2])
    return str(Path(__file__).resolve().parents[2])


def _resolve_ecosystem_root(ws_root: str) -> str:
    env_eco = os.environ.get("CLAIRE_ECOSYSTEM_ROOT")
    if env_eco:
        return os.path.abspath(env_eco)
    if os.path.isfile(os.path.join(ws_root, "workspaces/01_claire_architect_INTJ.md")):
        return ws_root
    parent = os.path.dirname(ws_root.rstrip("/"))
    if any(
        os.path.isdir(os.path.join(parent, w))
        for w in ALL_WORKSPACE_NAMES
        if os.path.join(parent, w) != ws_root.rstrip("/")
    ):
        return parent
    return ws_root


WORKSPACE_ROOT = _resolve_default_workspace_root()
ECOSYSTEM_ROOT = _resolve_ecosystem_root(WORKSPACE_ROOT)
AUDIT_LOG_PATH = os.environ.get(
    "CLAIRE_AUDIT_LOG_PATH", os.path.join(WORKSPACE_ROOT, "state/audit_log.jsonl")
)
CORRUPT_LOG_PATH = os.path.join(os.path.dirname(AUDIT_LOG_PATH), "audit_corrupt.jsonl")
HOOK_ERROR_LOG_PATH = os.path.join(WORKSPACE_ROOT, "state/hook_errors.log")
PRE_HASH_CACHE_PATH = os.path.join(WORKSPACE_ROOT, "state/.pre_tool_hashes.json")
EPISTEMIC_MANIFEST_PATH = os.path.join(WORKSPACE_ROOT, "state/epistemic_manifest.json")


def now_local(workspace_root: str | None = None, *, use_showcase_anchor: bool = False) -> datetime:
    ref_date = os.environ.get("CLAIRE_REFERENCE_DATE", "").strip()
    if ref_date:
        try:
            dt = datetime.fromisoformat(ref_date)
            return dt.replace(tzinfo=LOCAL_TZ) if dt.tzinfo is None else dt.astimezone(LOCAL_TZ)
        except ValueError:
            pass
    if use_showcase_anchor:
        ws_root = workspace_root or WORKSPACE_ROOT
        eco_root = _resolve_ecosystem_root(ws_root)
        if eco_root == ws_root and not os.path.isfile(os.path.join(ws_root, "SYSTEM_PROMPT.md")):
            sp_path = os.path.join(ws_root, "state/active_sprint.md")
            if os.path.isfile(sp_path):
                try:
                    with open(sp_path, "r", encoding="utf-8") as spf:
                        m_dt = re.search(
                            r"(?:\*\*Last Updated:\*\*|last_updated:)\s*(\d{4}-\d{2}-\d{2})",
                            spf.read(),
                            re.IGNORECASE,
                        )
                        if m_dt:
                            dt = datetime.fromisoformat(f"{m_dt.group(1)}T12:00:00")
                            return dt.replace(tzinfo=LOCAL_TZ)
                except Exception:
                    pass
    return datetime.now(LOCAL_TZ)


def lint_markdown_slop(target_path: str, text: str) -> str | None:
    if not target_path.lower().endswith(".md") or os.path.basename(target_path) == "human-voice.md":
        return None
    if "\u2014" in text or "\u2013" in text:
        return (
            "Anti-Slop Gate (human-voice.md): Em dash or en dash detected. "
            "Use commas, periods, colons, or parentheses instead."
        )
    if PSEUDO_WEIGHT_RE.search(text):
        return (
            "Anti-Slop Gate (human-voice.md): Unparsed '[WEIGHT: ...]' tag detected. "
            "Enforce invariants in Python hooks or state plain imperative rules."
        )
    if CONTRAST_FRAME_RE.search(text):
        return (
            "Anti-Slop Gate (human-voice.md): Negative-positive contrast frame detected. "
            "Skip the negation and state what the thing actually is."
        )
    match = BANNED_SLOP_TERMS_RE.search(text)
    if match:
        return (
            f"Anti-Slop Gate (human-voice.md): Banned AI filler/metaphor term "
            f"'{match.group(0)}' detected. Use plain, literal language."
        )
    return None


def lint_security_payload(
    target_label: str, text: str, *, check_stored_injection: bool = True
) -> str | None:
    """Reject hidden Unicode tag/bidi characters universally and stored prompt-injection payloads on state/Vault targets."""
    if not text:
        return None
    if HIDDEN_UNICODE_RE.search(text):
        return (
            f"Security Gate (Invisible Unicode / Bidi Injection): Payload for '{target_label}' "
            "contains hidden zero-width, bidirectional override, or Unicode Tag characters "
            "(U+E0000..U+E007F). Strip hidden control characters before writing."
        )
    if check_stored_injection:
        inj_m = STORED_INJECTION_RE.search(text)
        if inj_m:
            return (
                f"Security Gate (Stored Prompt Injection): Payload for '{target_label}' contains "
                f"system-rule override tags or prompt-injection directives ('{inj_m.group(0)}'). "
                "Blocked from entering persistent state or Vault."
            )
    return None


def file_sha256_and_size(path: str) -> tuple[str | None, int | None]:
    if not path or not os.path.isfile(path):
        return None, None
    try:
        h = hashlib.sha256()
        size = 0
        with open(path, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                h.update(chunk)
                size += len(chunk)
        return h.hexdigest(), size
    except OSError:
        return None, None


def git_head_short(cwd: str) -> str:
    """Pure-Python .git/HEAD resolver (<0.1ms, zero subprocess overhead)."""
    git_dir = os.path.join(cwd, ".git")
    head_file = os.path.join(git_dir, "HEAD")
    if not os.path.isfile(head_file):
        return "none"
    try:
        with open(head_file, "r", encoding="utf-8") as f:
            ref_line = f.read().strip()
        if not ref_line:
            return "uncommitted"
        if not ref_line.startswith("ref:"):
            return ref_line[:7]
        ref_rel = ref_line.split(":", 1)[1].strip()
        ref_path = os.path.join(git_dir, ref_rel)
        if os.path.isfile(ref_path):
            with open(ref_path, "r", encoding="utf-8") as rf:
                sha = rf.read().strip()
                return sha[:7] if sha else "uncommitted"
        packed_refs = os.path.join(git_dir, "packed-refs")
        if os.path.isfile(packed_refs):
            with open(packed_refs, "r", encoding="utf-8") as pf:
                for line in pf:
                    if line.startswith("#") or line.startswith("^"):
                        continue
                    parts = line.strip().split()
                    if len(parts) == 2 and parts[1] == ref_rel:
                        return parts[0][:7]
        return "uncommitted"
    except OSError:
        return "none"


def _record_pre_hash(
    conv_id: str, step_idx: Any, target_path: str, cache_path: str | None = None
) -> None:
    if not target_path:
        return
    c_path = cache_path or PRE_HASH_CACHE_PATH
    sha_b, bytes_b = file_sha256_and_size(target_path)
    resolved = os.path.realpath(target_path)
    exact_key = f"{conv_id}:{step_idx}:{resolved}"
    path_key = f"path:{resolved}"
    payload_val = {
        "sha256_before": sha_b[:16] if sha_b else None,
        "bytes_before": bytes_b,
    }
    try:
        os.makedirs(os.path.dirname(c_path), exist_ok=True)
        with open(c_path, "a+", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            f.seek(0)
            raw = f.read().strip()
            try:
                data = json.loads(raw) if raw else {}
                if not isinstance(data, dict):
                    data = {}
            except json.JSONDecodeError:
                data = {}
            if len(data) > 64:
                keys = list(data.keys())[-32:]
                data = {k: data[k] for k in keys}
            data[exact_key] = payload_val
            data[path_key] = payload_val
            f.seek(0)
            f.truncate()
            f.write(json.dumps(data))
            f.flush()
    except Exception:
        pass


def _pop_pre_hash(
    conv_id: str, step_idx: Any, target_path: str, cache_path: str | None = None
) -> tuple[str | None, int | None]:
    c_path = cache_path or PRE_HASH_CACHE_PATH
    if not target_path or not os.path.isfile(c_path):
        return None, None
    resolved = os.path.realpath(target_path)
    exact_key = f"{conv_id}:{step_idx}:{resolved}"
    path_key = f"path:{resolved}"
    try:
        with open(c_path, "a+", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            f.seek(0)
            raw = f.read().strip()
            if not raw:
                return None, None
            try:
                data = json.loads(raw)
                if not isinstance(data, dict):
                    data = {}
            except json.JSONDecodeError:
                f.seek(0)
                f.truncate()
                f.write("{}")
                f.flush()
                return None, None
            entry = data.pop(exact_key, None)
            fallback = data.pop(path_key, None)
            chosen = entry if entry is not None else fallback
            f.seek(0)
            f.truncate()
            f.write(json.dumps(data))
            f.flush()
            if chosen:
                return chosen.get("sha256_before"), chosen.get("bytes_before")
    except Exception:
        pass
    return None, None


def _quarantine_corrupt_tail(log_path: str, bad_line: str) -> None:
    try:
        corrupt_path = os.path.join(os.path.dirname(log_path), "audit_corrupt.jsonl")
        ts = now_local().isoformat(timespec="seconds")
        with open(corrupt_path, "a", encoding="utf-8") as cf:
            cf.write(json.dumps({"quarantined_at": ts, "raw": bad_line}) + "\n")
    except Exception:
        pass


def _read_tail_hash_from_bak(log_path: str) -> tuple[str, bool]:
    bak_files = sorted(glob.glob(f"{log_path}.*.bak"), reverse=True)
    for bak in bak_files:
        try:
            with open(bak, "rb") as bf:
                bf.seek(0, os.SEEK_END)
                end = bf.tell()
                if end == 0:
                    continue
                bf.seek(max(0, end - 8192))
                for line in reversed(bf.read().decode("utf-8", errors="replace").splitlines()):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        h = str(obj.get("hash") or "")
                        if len(h) == 64:
                            return h, True
                    except json.JSONDecodeError:
                        continue
        except OSError:
            continue
    return GENESIS_HASH, False


def read_last_hash_from_fd(f: Any, log_path: str = "") -> tuple[str, bool]:
    """Read tail hash from locked fd, self-healing partial/corrupt tail lines and checking .bak if empty."""
    f.seek(0, os.SEEK_END)
    end = f.tell()
    if end == 0:
        if log_path:
            return _read_tail_hash_from_bak(log_path)
        return GENESIS_HASH, False

    seek_pos = max(0, end - 16384)
    f.seek(seek_pos)
    chunk = f.read()
    lines = chunk.splitlines(keepends=True)
    bytes_to_trim = 0
    found_hash: str | None = None

    for raw_line in reversed(lines):
        stripped = raw_line.decode("utf-8", errors="replace").strip()
        if not stripped:
            bytes_to_trim += len(raw_line)
            continue
        try:
            obj = json.loads(stripped)
            h = str(obj.get("hash") or "")
            if len(h) == 64:
                found_hash = h
                break
        except json.JSONDecodeError:
            pass
        if log_path:
            _quarantine_corrupt_tail(log_path, stripped)
        bytes_to_trim += len(raw_line)

    if bytes_to_trim > 0:
        new_end = max(0, end - bytes_to_trim)
        f.seek(new_end)
        f.truncate()
        f.flush()

    if found_hash:
        return found_hash, False
    if log_path:
        return _read_tail_hash_from_bak(log_path)
    return GENESIS_HASH, False


def _prune_old_bak_segments(log_path: str) -> None:
    bak_files = sorted(glob.glob(f"{log_path}.*.bak"))
    if len(bak_files) > MAX_BAK_SEGMENTS:
        for old_bak in bak_files[: len(bak_files) - MAX_BAK_SEGMENTS]:
            try:
                os.remove(old_bak)
            except OSError:
                pass


def append_locked_audit_entry(log_path: str, entry_core: dict[str, Any]) -> dict[str, Any]:
    """Inode-safe, fcntl.flock-serialized append with self-healing tail, 10 MB rotation, fsync, and 5-bak cap."""
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "a+b") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        prev_hash, from_bak = read_last_hash_from_fd(f, log_path)
        f.seek(0, os.SEEK_END)
        file_size = f.tell()
        rotated_now = False

        if file_size >= MAX_AUDIT_LOG_BYTES:
            ts_tag = now_local().strftime("%Y%m%d_%H%M%S_%f")
            rotated_path = f"{log_path}.{ts_tag}.bak"
            f.flush()
            os.fsync(f.fileno())
            shutil.copyfile(log_path, rotated_path)
            _prune_old_bak_segments(log_path)
            f.seek(0)
            f.truncate()
            os.fsync(f.fileno())
            rotated_now = True

        if rotated_now or from_bak:
            entry_core["rotated_checkpoint"] = True

        entry_core["prev_hash"] = prev_hash
        canonical = json.dumps(entry_core, sort_keys=True, separators=(",", ":"))
        entry_core["hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        line_bytes = (json.dumps(entry_core, separators=(",", ":")) + "\n").encode("utf-8")
        f.seek(0, os.SEEK_END)
        f.write(line_bytes)
        f.flush()
    return entry_core


def _log_hook_error(context: str, exc: Exception, err_log_path: str | None = None) -> None:
    target_err = err_log_path or HOOK_ERROR_LOG_PATH
    try:
        os.makedirs(os.path.dirname(target_err), exist_ok=True)
        if os.path.isfile(target_err) and os.path.getsize(target_err) > 65536:
            os.remove(target_err)
        ts = now_local().isoformat(timespec="seconds")
        with open(target_err, "a", encoding="utf-8") as ef:
            ef.write(f"[{ts}] {context}: {type(exc).__name__}: {exc}\n")
    except Exception:
        pass


def _classify_file_target(path: str, workspace_root: str | None = None) -> str:
    norm = path.replace("\\", "/")
    ws_norm = (workspace_root or WORKSPACE_ROOT).replace("\\", "/").rstrip("/") + "/"
    if norm.startswith(ws_norm):
        rel = norm[len(ws_norm) :]
        if rel.startswith("scratch/"):
            return "ARTIFACT_STAGE"
        if rel.startswith("state/") and rel.endswith((".md", ".json")):
            return "STATE_CACHE"
        return "CODE_SYSTEM"
    if "/scratch/" in norm or "/brain/" in norm:
        return "ARTIFACT_STAGE"
    if "/state/" in norm and norm.endswith((".md", ".json")):
        return "STATE_CACHE"
    return "CODE_SYSTEM"


def _redact_command(cmd: str) -> str:
    cleaned = cmd.strip()
    cleaned = SECRET_FLAG_REDACT_RE.sub(r"\1[REDACTED]", cleaned)
    cleaned = QUICK_ADD_REDACT_RE.sub(r"\1[REDACTED]", cleaned)
    cleaned = EMAIL_REDACT_RE.sub("[EMAIL_REDACTED]", cleaned)
    cleaned = PHONE_REDACT_RE.sub("[PHONE_REDACTED]", cleaned)
    if len(cleaned) > 220:
        return cleaned[:217] + "..."
    return cleaned


def _extract_command_context_tags(cmd: str) -> str:
    dt_match = ISO_DATETIME_RE.search(cmd)
    return f" [when={dt_match.group(1)}]" if dt_match else ""


def _classify_command(cmd: str) -> str:
    lower = cmd.lower()
    if "gcalendar" in lower:
        return "EXTERNAL_ACTION" if GCAL_MUTATE_RE.search(cmd) else "READ_QUERY"
    if "gdrive" in lower:
        return "EXTERNAL_ACTION" if GDRIVE_MUTATE_RE.search(cmd) else "READ_QUERY"
    return "EXEC_CMD"


def _is_pure_self_observer_command(cmd: str) -> bool:
    stripped = BENIGN_REDIRECT_RE.sub("", cmd).strip()
    if COMPOUND_SHELL_OP_RE.search(stripped):
        return False
    return bool(SELF_OBSERVER_ANCHORED_RE.match(stripped))


def _shell_targets_locked_path(cmd: str, locked_rel_paths: tuple[str, ...] = ()) -> str | None:
    """Detect shell commands that write to or overwrite a locked file, including quoted paths."""
    norm_cmd = cmd.replace("\\", "/")

    if re.search(r"\biterate_stage\.py['\"]?\s+apply\b", norm_cmd):
        if not re.search(r"\biterate_stage\.py['\"]?\s+apply\b[^;&|\n]*--dry-run\b", norm_cmd):
            return "iterate_stage.py apply"

    norm_cmd = re.sub(r"['\"]?[^\s'\"`|;&>]*/scratch/iterate_stage/[^\s'\"`|;&>]*['\"]?", "STAGE_PATH", norm_cmd)

    stripped = BENIGN_REDIRECT_RE.sub(" ", norm_cmd)
    scan_text = SECRET_FLAG_REDACT_RE.sub(r"\1REDACTED_ARG", stripped)
    is_inline_eval = bool(re.search(r"\b(?:python3?\s+-c|sh\s+-c|bash\s+-c|zsh\s+-c)\b", scan_text))

    for rel in locked_rel_paths:
        base = os.path.basename(rel)
        pat_target = rf"['\"]?(?:[^\s;|\n'\"]*/)?(?:{re.escape(rel)}|{re.escape(base)})['\"]?(?=$|[\s;|\n'\"\)])"

        if re.search(rf"(?:>>?|(?<!-)\btee\b(?:\s+-a)?)\s*{pat_target}", scan_text):
            return base

        if re.search(
            rf"\b(?:sed\s+-i|perl\s+-i|cp|mv|rm|dd|install|rsync|ln|truncate)\b[^;&|\n]*{pat_target}",
            scan_text,
        ):
            return base

        if is_inline_eval and re.search(
            rf"(?:shutil\.copy|os\.replace|os\.rename|\.write_text|\.write_bytes|open\([^)]*['\"][wa+]['\"])[^;&|\n]*{pat_target}",
            scan_text,
        ):
            return base

    return None


def _atomic_stamp_state_cache_date(path: str) -> None:
    """Atomically stamp **Last Updated:** YYYY-MM-DD in local time on mutated STATE_CACHE .md files."""
    today_iso = now_local().date().isoformat()
    dir_name = os.path.dirname(os.path.abspath(path))
    with open(path, "r", encoding="utf-8") as sf:
        txt = sf.read()
    updated_txt, n_sub = re.subn(
        r"(\*\*Last Updated:\*\*\s*)\d{4}-\d{2}-\d{2}",
        rf"\g<1>{today_iso}",
        txt,
        count=1,
    )
    if not n_sub or updated_txt == txt:
        return
    fd, tmp_path = tempfile.mkstemp(prefix=".stamp_", suffix=".md", dir=dir_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tf:
            tf.write(updated_txt)
            tf.flush()
            os.fsync(tf.fileno())
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def _resolve_kortex_label(uuid_or_title: str) -> str:
    clean = uuid_or_title.strip()
    if not clean:
        return ""
    return KORTEX_UUID_LABELS.get(clean, clean[:36])


def _load_epistemic_manifest(manifest_path: str | None = None) -> dict[str, Any]:
    m_path = manifest_path or EPISTEMIC_MANIFEST_PATH
    if not os.path.isfile(m_path):
        return {}
    try:
        with open(m_path, "r", encoding="utf-8") as mf:
            obj = json.loads(mf.read() or "{}")
            return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _extract_file_mutation_text(args: dict[str, Any]) -> str:
    """Recursively extract all replacement/content strings from write_file and edit_file payloads."""
    parts: list[str] = []
    for k in ("Content", "Replacement", "ReplacementContent"):
        v = args.get(k)
        if isinstance(v, str) and v:
            parts.append(v)
    chunks = args.get("ReplacementChunks")
    if isinstance(chunks, list):
        for ch in chunks:
            if isinstance(ch, dict):
                for ck in ("ReplacementContent", "Replacement", "Content"):
                    cv = ch.get(ck)
                    if isinstance(cv, str) and cv:
                        parts.append(cv)
    return "\n".join(parts)


def _user_input_authorizes_command(user_input: str, matched_bin: str) -> bool:
    """Require strict word-boundary token match so 'nc' in 'since'/'once' never bypasses taint checks."""
    if not user_input or not matched_bin:
        return False
    pattern = rf"(?<![A-Za-z0-9_.-]){re.escape(matched_bin)}(?![A-Za-z0-9_.-])"
    return bool(re.search(pattern, user_input, re.IGNORECASE))


def _extract_verified_host(domain_or_url: str) -> str:
    clean = (domain_or_url or "").strip().lower()
    if not clean:
        return ""
    if "://" in clean:
        try:
            return (urlparse(clean).hostname or "").lower()
        except Exception:
            return ""
    return clean.split("/")[0].split("?")[0].split("#")[0].split(":")[0]


def _host_matches_domain_list(host: str, domain_list: tuple[str, ...]) -> str | None:
    if not host:
        return None
    for d in domain_list:
        if host == d or host.endswith("." + d):
            return d
    return None


def _hash_payload_snippet(obj: Any) -> tuple[str | None, int | None]:
    if obj is None:
        return None, None
    try:
        raw = obj if isinstance(obj, str) else json.dumps(obj, sort_keys=True, separators=(",", ":"))
        raw_bytes = raw.encode("utf-8")
        if not raw_bytes:
            return None, None
        return hashlib.sha256(raw_bytes).hexdigest()[:16], len(raw_bytes)
    except Exception:
        return None, None


def _classify_exogenous_lookup(tool_name: str, args: dict[str, Any]) -> tuple[str, str, str]:
    """Classify an external lookup using strict URL/domain hostname parsing to prevent query-string spoofing."""
    if tool_name in (
        "mcp_gemini-api_gemini-api-docs_gemini_search_docs",
        "mcp_gemini-api_gemini-api-docs_gemini_get_doc",
    ):
        q_str = str(args.get("chunk_id") or args.get("query") or "").strip()
        src_scope = str(args.get("source") or args.get("scope") or "gemini-api-docs").strip()
        return "TIER_1_UPSTREAM_SPEC", src_scope, q_str

    if tool_name == "search_marketplace":
        q_list = args.get("queries") or []
        q_str = ", ".join(str(x) for x in q_list[:3]) if isinstance(q_list, list) else str(q_list)
        return "TIER_2_EMPIRICAL_PRACTITIONER", "agy-marketplace", q_str

    domain_arg = str(args.get("domain") or "").strip().lower()
    url_arg = str(args.get("Url") or args.get("url") or "").strip()
    query_arg = str(args.get("query") or url_arg or "").strip()

    verified_host = _extract_verified_host(url_arg) or _extract_verified_host(domain_arg)
    if verified_host:
        t1_match = _host_matches_domain_list(verified_host, TIER_1_SPEC_DOMAINS)
        if t1_match:
            return "TIER_1_UPSTREAM_SPEC", t1_match, query_arg
        t2_match = _host_matches_domain_list(verified_host, TIER_2_PRACTITIONER_DOMAINS)
        if t2_match:
            return "TIER_2_EMPIRICAL_PRACTITIONER", t2_match, query_arg
        return "TIER_3_UNVERIFIED_WEB", verified_host, query_arg

    return "TIER_3_UNVERIFIED_WEB", "general-web", query_arg


def apply_kortex_defaults(
    tool_args: dict[str, Any],
    kortex_tool: str,
    default_notebook_id: str | None = None,
) -> tuple[dict[str, Any], bool]:
    updated = dict(tool_args)
    modified = False
    if kortex_tool == "ask_question":
        if updated.get("citation_format") not in ("none", "inline"):
            updated["citation_format"] = "none"
            modified = True
        if default_notebook_id and not updated.get("notebook_id"):
            updated["notebook_id"] = default_notebook_id
            modified = True
    if kortex_tool and kortex_tool not in ACCOUNTLESS_KORTEX_TOOLS and not updated.get("account"):
        updated["account"] = DEFAULT_KORTEX_ACCOUNT
        modified = True
    return updated, modified


def evaluate_base_pre_tool_use(
    payload: dict[str, Any],
    *,
    workspace_root: str,
    locked_paths: set[str],
    locked_rel_paths: tuple[str, ...],
    debug_phrase: str,
    default_notebook_id: str | None = None,
) -> dict[str, Any]:
    """Universal PreToolUse guard (file/shell locks, Unicode/injection gate, slop lint, CaMeL taint, Kortex defaults)."""
    tool_call = payload.get("toolCall") or {}
    tool_name = tool_call.get("name", "")
    args = tool_call.get("args") or {}
    last_user_input = (payload.get("lastUserInput") or "").lstrip()
    in_debug_mode = last_user_input.lower().startswith(debug_phrase.lower())
    user_input_sha = (
        hashlib.sha256(last_user_input.encode("utf-8")).hexdigest()[:16] if last_user_input else "empty"
    )
    conv_id = str(payload.get("conversationId") or "local")[:12]
    step_idx = payload.get("stepIdx")
    manifest_path = os.path.join(workspace_root, "state/epistemic_manifest.json")
    pre_hash_path = os.path.join(workspace_root, "state/.pre_tool_hashes.json")

    if tool_name in ("edit_file", "write_file"):
        target_path = args.get("Path") or ""
        if target_path:
            resolved = os.path.realpath(target_path)
            if resolved in locked_paths and not in_debug_mode:
                return {
                    "decision": "deny",
                    "reason": (
                        f"Core Architecture Lock: '{os.path.basename(target_path)}' is locked "
                        f"during normal operation. The user prompt must begin with "
                        f"'{debug_phrase}' to modify core identity, rule, or hook files."
                    ),
                }
            body_text = _extract_file_mutation_text(args)
            is_state_cache = _classify_file_target(target_path, workspace_root) == "STATE_CACHE"
            sec_err = lint_security_payload(
                os.path.basename(target_path),
                body_text,
                check_stored_injection=is_state_cache,
            )
            if sec_err:
                return {"decision": "deny", "reason": sec_err}
            slop_err = lint_markdown_slop(target_path, body_text)
            if slop_err:
                return {"decision": "deny", "reason": slop_err}
            _record_pre_hash(conv_id, step_idx, target_path, cache_path=pre_hash_path)

    if tool_name == "run_command":
        cmd_line = str(args.get("CommandLine") or "")
        locked_hit = _shell_targets_locked_path(cmd_line, locked_rel_paths)
        if locked_hit and not in_debug_mode:
            return {
                "decision": "deny",
                "reason": (
                    f"Core Architecture Lock (Shell Guard): Command appears to mutate locked file "
                    f"'{locked_hit}'. Start prompt with '{debug_phrase}' to modify locked files."
                ),
            }
        exfil_m = EXFIL_AND_SLOPSQUAT_CMD_RE.search(cmd_line)
        if exfil_m:
            m_data = _load_epistemic_manifest(manifest_path)
            c_meta = (m_data.get("conv_reads") or {}).get(conv_id) or {}
            taint_list = c_meta.get("taint_sources") or []
            taint_turn_sha = c_meta.get("taint_user_input_sha256")
            has_sticky_injection = "INJECTION_DETECTED_IN_RESPONSE" in taint_list
            active_taint = (
                taint_list
                if (has_sticky_injection or not taint_turn_sha or taint_turn_sha == user_input_sha)
                else []
            )
            matched_bin = re.sub(r"\s+", " ", exfil_m.group(1).strip().lower()).rstrip("(").strip()
            if active_taint and not _user_input_authorizes_command(last_user_input, matched_bin):
                return {
                    "decision": "deny",
                    "reason": (
                        f"CaMeL Taint & Lethal-Trifecta Gate: Conversation has ingested untrusted "
                        f"external content ({', '.join(active_taint)}). Outbound network/exfiltration or "
                        f"package-install token '{matched_bin}' is blocked unless explicitly "
                        "requested as a standalone token in the user's current prompt or cleared via "
                        "'audit_log.py epistemic --clear-taint'."
                    ),
                }

    if tool_name == "call_mcp_tool" and args.get("ServerName") == "kortex":
        kortex_tool = args.get("ToolName", "")
        tool_args = dict(args.get("Arguments") or {})
        if kortex_tool in KORTEX_MUTATION_TOOLS:
            kortex_payload_text = json.dumps(tool_args, ensure_ascii=False)
            sec_err = lint_security_payload(f"kortex.{kortex_tool}", kortex_payload_text)
            if sec_err:
                return {"decision": "deny", "reason": sec_err}
        new_args, modified = apply_kortex_defaults(tool_args, kortex_tool, default_notebook_id)
        if modified:
            return {"decision": "allow", "overwrite": {"Arguments": new_args}}

    return {"decision": "allow"}


def _update_epistemic_manifest(
    *,
    ts_iso: str,
    conv_id: str,
    manifest_path: str | None = None,
    uuids: list[str] | None = None,
    category: str = "",
    tool_label: str = "",
    read_sha256: str | None = None,
    sync_sha256: str | None = None,
    local_state_mutated: bool = False,
    state_file_mutated: str | None = None,
    gcal_scope_read: str | None = None,
    rpl_staged: str | None = None,
    rpl_sent: str | None = None,
    user_input_sha256: str | None = None,
    external_lookup: dict[str, Any] | None = None,
    runtime_conformance_ran: bool = False,
    taint_source: str | None = None,
    security_incident: dict[str, Any] | None = None,
    primary_vault_uuid: str | None = None,
) -> None:
    m_path = manifest_path or EPISTEMIC_MANIFEST_PATH
    valid_uuids = [u.strip() for u in (uuids or []) if u and u.strip() in KORTEX_UUID_LABELS]
    expanded_conv_uuids = set(valid_uuids)
    for u in valid_uuids:
        parent_u = (VAULT_SOURCE_SPECS.get(u) or {}).get("parent")
        if parent_u:
            expanded_conv_uuids.add(parent_u)

    if not (
        valid_uuids
        or local_state_mutated
        or state_file_mutated
        or gcal_scope_read
        or rpl_staged
        or rpl_sent
        or external_lookup
        or runtime_conformance_ran
        or taint_source
        or security_incident
    ):
        return

    try:
        os.makedirs(os.path.dirname(m_path), exist_ok=True)
        with open(m_path, "a+", encoding="utf-8") as mf:
            fcntl.flock(mf.fileno(), fcntl.LOCK_EX)
            mf.seek(0)
            raw = mf.read().strip()
            try:
                data = json.loads(raw) if raw else {}
                if not isinstance(data, dict):
                    data = {}
            except json.JSONDecodeError:
                data = {}

            sources_map = data.setdefault("vault_sources", {})
            for uid in valid_uuids:
                rec = sources_map.setdefault(
                    uid,
                    {
                        "label": KORTEX_UUID_LABELS.get(uid, uid),
                        "last_read_ts": None,
                        "last_sync_ts": None,
                        "read_sha256": None,
                        "sync_sha256": None,
                        "remote_drift_detected": False,
                        "last_conv_id": None,
                        "last_tool": None,
                    },
                )
                if category == "VAULT_SYNC":
                    rec["last_sync_ts"] = ts_iso
                    if sync_sha256:
                        rec["sync_sha256"] = sync_sha256
                        rec["read_sha256"] = sync_sha256
                    rec["remote_drift_detected"] = False
                    if primary_vault_uuid and primary_vault_uuid in expanded_conv_uuids:
                        data["latest_vault_sync_ts"] = ts_iso
                    elif not data.get("latest_vault_sync_ts"):
                        data["latest_vault_sync_ts"] = ts_iso
                elif category == "READ_QUERY":
                    prev_baseline = rec.get("sync_sha256") or rec.get("read_sha256")
                    if read_sha256 and prev_baseline and read_sha256 != prev_baseline:
                        rec["remote_drift_detected"] = True
                    rec["last_read_ts"] = ts_iso
                    if read_sha256:
                        rec["read_sha256"] = read_sha256
                rec["last_conv_id"] = conv_id
                rec["last_tool"] = tool_label

            if local_state_mutated or state_file_mutated:
                data["latest_local_state_mut_ts"] = ts_iso

            if (
                expanded_conv_uuids
                or gcal_scope_read
                or external_lookup
                or runtime_conformance_ran
                or taint_source
            ):
                conv_map = data.setdefault("conv_reads", {})
                if len(conv_map) > 32:
                    keep_keys = list(conv_map.keys())[-16:]
                    data["conv_reads"] = {k: conv_map[k] for k in keep_keys}
                    conv_map = data["conv_reads"]
                c_entry = conv_map.setdefault(
                    conv_id,
                    {
                        "kortex_uuids": [],
                        "gcal_scopes": [],
                        "external_tiers": [],
                        "taint_sources": [],
                        "runtime_conformance": False,
                    },
                )
                if category == "READ_QUERY" and expanded_conv_uuids:
                    cur_u = set(c_entry.get("kortex_uuids") or [])
                    c_entry["kortex_uuids"] = sorted(cur_u | expanded_conv_uuids)
                if gcal_scope_read:
                    cur_g = set(c_entry.get("gcal_scopes") or [])
                    cur_g.add(gcal_scope_read)
                    c_entry["gcal_scopes"] = sorted(cur_g)
                if external_lookup and external_lookup.get("tier"):
                    cur_t = set(c_entry.get("external_tiers") or [])
                    cur_t.add(str(external_lookup["tier"]))
                    c_entry["external_tiers"] = sorted(cur_t)
                if taint_source:
                    prev_turn_sha = c_entry.get("taint_user_input_sha256")
                    cur_ts = set(c_entry.get("taint_sources") or [])
                    if user_input_sha256 and prev_turn_sha and prev_turn_sha != user_input_sha256:
                        cur_ts = {t for t in cur_ts if t == "INJECTION_DETECTED_IN_RESPONSE"}
                    cur_ts.add(taint_source)
                    c_entry["taint_sources"] = sorted(cur_ts)
                    if user_input_sha256:
                        c_entry["taint_user_input_sha256"] = user_input_sha256
                if runtime_conformance_ran:
                    c_entry["runtime_conformance"] = True

            if external_lookup:
                ext_list = data.setdefault("external_lookups", [])
                if not isinstance(ext_list, list):
                    ext_list = []
                ext_list.append(external_lookup)
                if len(ext_list) > MAX_EXTERNAL_LOOKUPS:
                    ext_list = ext_list[-MAX_EXTERNAL_LOOKUPS:]
                data["external_lookups"] = ext_list

            if security_incident:
                sec_list = data.setdefault("security_incidents", [])
                if not isinstance(sec_list, list):
                    sec_list = []
                sec_list.append(security_incident)
                if len(sec_list) > MAX_SECURITY_INCIDENTS:
                    sec_list = sec_list[-MAX_SECURITY_INCIDENTS:]
                data["security_incidents"] = sec_list

            if rpl_staged or rpl_sent:
                rpl_map = data.setdefault("staged_replies", {})
                if len(rpl_map) > 64:
                    keep_r = list(rpl_map.keys())[-32:]
                    data["staged_replies"] = {k: rpl_map[k] for k in keep_r}
                    rpl_map = data["staged_replies"]
                if rpl_staged:
                    existing_r = rpl_map.get(rpl_staged)
                    if not isinstance(existing_r, dict):
                        existing_r = {}
                    existing_r.setdefault("ts", ts_iso)
                    existing_r["conv_id"] = conv_id
                    existing_r["status"] = "STAGED"
                    if user_input_sha256:
                        existing_r["user_input_sha256"] = user_input_sha256
                    rpl_map[rpl_staged] = existing_r
                if rpl_sent:
                    existing_r = rpl_map.get(rpl_sent)
                    if not isinstance(existing_r, dict):
                        existing_r = {"ts": ts_iso, "conv_id": conv_id}
                    existing_r["status"] = "SENT"
                    existing_r["sent_ts"] = ts_iso
                    rpl_map[rpl_sent] = existing_r

            data["updated_at"] = ts_iso
            mf.seek(0)
            mf.truncate()
            mf.write(json.dumps(data, indent=2) + "\n")
            mf.flush()
    except Exception:
        pass


def _inspect_response_for_injection(
    ts_iso: str,
    conv_id: str,
    source_label: str,
    resp_obj: Any,
    *,
    check_stored_injection: bool = True,
) -> dict[str, Any] | None:
    if resp_obj is None:
        return None
    try:
        raw_str = resp_obj if isinstance(resp_obj, str) else json.dumps(resp_obj, ensure_ascii=False)
    except Exception:
        return None
    sec_err = lint_security_payload(
        source_label, raw_str, check_stored_injection=check_stored_injection
    )
    if not sec_err:
        return None
    return {
        "ts": ts_iso,
        "conv_id": conv_id,
        "source": source_label,
        "alert": sec_err,
    }


def handle_post_tool_use(
    payload: dict[str, Any],
    *,
    workspace_root: str | None = None,
    audit_log_path: str | None = None,
    manifest_path: str | None = None,
    pre_hash_path: str | None = None,
    cmd_redactor: Callable[[str], str] | None = None,
    cmd_classifier: Callable[[str], str] | None = None,
    cmd_tag_extractor: Callable[[str], str] | None = None,
    post_tool_enricher: Callable[[dict[str, Any], str, str, str, str, str], dict[str, Any]] | None = None,
    primary_vault_uuid: str | None = None,
) -> dict[str, Any]:
    ws_root = workspace_root or WORKSPACE_ROOT
    a_log_path = audit_log_path or (
        AUDIT_LOG_PATH if ws_root == WORKSPACE_ROOT else os.path.join(ws_root, "state/audit_log.jsonl")
    )
    m_path = manifest_path or (
        EPISTEMIC_MANIFEST_PATH
        if ws_root == WORKSPACE_ROOT
        else os.path.join(ws_root, "state/epistemic_manifest.json")
    )
    p_hash_path = pre_hash_path or (
        PRE_HASH_CACHE_PATH
        if ws_root == WORKSPACE_ROOT
        else os.path.join(ws_root, "state/.pre_tool_hashes.json")
    )
    err_log_path = (
        HOOK_ERROR_LOG_PATH
        if ws_root == WORKSPACE_ROOT
        else os.path.join(ws_root, "state/hook_errors.log")
    )
    redact_fn = cmd_redactor or _redact_command
    classify_fn = cmd_classifier or _classify_command
    tags_fn = cmd_tag_extractor or _extract_command_context_tags

    tool_call = payload.get("toolCall") or {}
    tool_name = tool_call.get("name", "")
    if not tool_name:
        return {}

    args = tool_call.get("args") or {}
    error_str = str(payload.get("error") or payload.get("toolError") or "").strip()
    if not error_str:
        resp_probe = payload.get("toolResponse") or payload.get("response") or payload.get("result") or payload.get("output")
        if isinstance(resp_probe, dict):
            if resp_probe.get("isError") is True or resp_probe.get("error"):
                error_str = str(resp_probe.get("error") or resp_probe.get("content") or "MCP tool error")[:160]
            elif resp_probe.get("exitCode") not in (None, 0) or resp_probe.get("returnCode") not in (None, 0):
                ec = resp_probe.get("exitCode") if resp_probe.get("exitCode") is not None else resp_probe.get("returnCode")
                error_str = f"exit code {ec}"
        if not error_str and resp_probe is not None:
            probe_str = resp_probe if isinstance(resp_probe, str) else json.dumps(resp_probe, ensure_ascii=False)
            exit_m = re.search(r"(?:Command exited with code|exit code:?)\s+([1-9]\d*)\b", probe_str, re.IGNORECASE)
            if exit_m:
                error_str = f"exit code {exit_m.group(1)}"
    status = f"ERROR: {error_str[:160]}" if error_str else "OK"
    last_user_input = (payload.get("lastUserInput") or "").lstrip()
    user_input_sha = (
        hashlib.sha256(last_user_input.encode("utf-8")).hexdigest()[:16] if last_user_input else "empty"
    )
    conv_id = str(payload.get("conversationId") or "local")[:12]
    step_idx = payload.get("stepIdx")
    ts_iso = now_local().isoformat(timespec="seconds")

    category = "EXEC_CMD"
    target = tool_name
    summary = ""
    sha256_before: str | None = None
    bytes_before: int | None = None
    sha256_after: str | None = None
    bytes_after: int | None = None
    mutated: bool | None = None

    if tool_name in ("edit_file", "write_file"):
        raw_path = str(args.get("Path") or "")
        target = raw_path
        category = _classify_file_target(raw_path, ws_root)
        sha256_before, bytes_before = _pop_pre_hash(conv_id, step_idx, raw_path, cache_path=p_hash_path)
        sha_full, bytes_after = file_sha256_and_size(raw_path)
        sha256_after = sha_full[:16] if sha_full else None
        mutated = (sha256_before != sha256_after) if sha256_after is not None else False
        delta_b = (bytes_after or 0) - (bytes_before or 0)
        if (
            mutated
            and not error_str
            and category == "STATE_CACHE"
            and raw_path.endswith(".md")
            and os.path.isfile(raw_path)
        ):
            if bytes_before is None or abs(delta_b) >= SUBSTANTIVE_DATE_STAMP_DELTA_BYTES:
                try:
                    _atomic_stamp_state_cache_date(raw_path)
                    sha_full, bytes_after = file_sha256_and_size(raw_path)
                    sha256_after = sha_full[:16] if sha_full else None
                    delta_b = (bytes_after or 0) - (bytes_before or 0)
                except Exception:
                    pass
            _update_epistemic_manifest(
                ts_iso=ts_iso,
                conv_id=conv_id,
                manifest_path=m_path,
                local_state_mutated=True,
                state_file_mutated=os.path.basename(raw_path),
            )
        summary = f"{tool_name} ({bytes_after or 0}B, delta={delta_b:+d}B, mutated={mutated})"
    elif tool_name == "run_command":
        cmd_line = str(args.get("CommandLine") or "")
        if _is_pure_self_observer_command(cmd_line) and not error_str:
            return {}
        base_summary = str(args.get("toolSummary") or "").strip()
        category = classify_fn(cmd_line)
        target = redact_fn(cmd_line)
        summary = base_summary + tags_fn(cmd_line)
        if not error_str:
            low_cmd = cmd_line.lower()
            resp_obj = payload.get("toolResponse") or payload.get("response") or payload.get("result")
            taint_src = None
            if (
                category == "READ_QUERY"
                and "gdrive" in low_cmd
                and (UNTRUSTED_INBOX_ID.lower() in low_cmd or "00_antigravity_inbox" in low_cmd)
            ):
                taint_src = "GDRIVE_UNTRUSTED_INBOX"
            conformance_ran = category == "EXEC_CMD"
            extra_manifest: dict[str, Any] = {}
            if post_tool_enricher is not None:
                extra_manifest = post_tool_enricher(payload, category, target, summary, ts_iso, conv_id)
                if extra_manifest.get("summary_override"):
                    summary = str(extra_manifest.pop("summary_override"))
                if extra_manifest.get("taint_source"):
                    taint_src = str(extra_manifest.pop("taint_source"))
            sec_inc = (
                _inspect_response_for_injection(ts_iso, conv_id, target[:48], resp_obj)
                if taint_src
                else None
            )
            if sec_inc:
                taint_src = "INJECTION_DETECTED_IN_RESPONSE"
            if conformance_ran or taint_src or sec_inc or extra_manifest:
                _update_epistemic_manifest(
                    ts_iso=ts_iso,
                    conv_id=conv_id,
                    manifest_path=m_path,
                    category=category,
                    user_input_sha256=user_input_sha if (taint_src or extra_manifest.get("rpl_staged")) else None,
                    runtime_conformance_ran=conformance_ran,
                    taint_source=taint_src,
                    security_incident=sec_inc,
                    **extra_manifest,
                )
    elif tool_name in EXOGENOUS_LOOKUP_TOOLS:
        category = "EXTERNAL_LOOKUP"
        mutated = False
        tier, src_label, q_sum = _classify_exogenous_lookup(tool_name, args)
        target = f"{tool_name}:{src_label}"
        resp_obj = payload.get("toolResponse") or payload.get("response") or payload.get("result")
        sha256_after, bytes_after = _hash_payload_snippet(resp_obj)
        summary = f"[{tier}] {q_sum}"[:220]
        if not error_str:
            is_untrusted_web = tier == "TIER_3_UNVERIFIED_WEB"
            taint_src = f"WEB:{src_label}" if is_untrusted_web else None
            sec_inc = _inspect_response_for_injection(
                ts_iso,
                conv_id,
                target,
                resp_obj,
                check_stored_injection=True,
            )
            if sec_inc:
                taint_src = "INJECTION_DETECTED_IN_RESPONSE"
            _update_epistemic_manifest(
                ts_iso=ts_iso,
                conv_id=conv_id,
                manifest_path=m_path,
                category=category,
                tool_label=target,
                user_input_sha256=user_input_sha if taint_src else None,
                external_lookup={
                    "ts": ts_iso,
                    "conv_id": conv_id,
                    "tool": tool_name,
                    "tier": tier,
                    "source": src_label,
                    "query": q_sum[:140],
                    "sha256": sha256_after,
                },
                taint_source=taint_src,
                security_incident=sec_inc,
            )
    elif tool_name == "call_mcp_tool":
        server = str(args.get("ServerName") or "")
        mcp_tool = str(args.get("ToolName") or "")
        mcp_args = args.get("Arguments") or {}
        target = f"{server}.{mcp_tool}"
        touched_uuids: list[str] = []
        read_sha: str | None = None
        sync_sha: str | None = None

        if server == "kortex" and mcp_tool in KORTEX_MUTATION_TOOLS:
            if mcp_tool == "bulk":
                norm_ops = normalize_kortex_bulk_ops(mcp_args)
                mut_ops = [
                    (op_t, op_a)
                    for op_t, op_a in norm_ops
                    if op_t in KORTEX_MUTATION_TOOLS
                ]
                if mut_ops:
                    category = "VAULT_SYNC"
                    mutated = True
                    labels = []
                    bulk_texts: list[str] = []
                    for _op_t, op_args in mut_ops[:3]:
                        for k_id in ("source_id", "notebook_id", "note_id"):
                            val_id = str(op_args.get(k_id) or "")
                            if val_id:
                                touched_uuids.append(val_id)
                        body_c = (
                            op_args.get("content")
                            or op_args.get("text")
                            or op_args.get("note")
                            or op_args.get("highlight")
                            or op_args.get("new_title")
                        )
                        if isinstance(body_c, str) and body_c:
                            bulk_texts.append(body_c)
                        t_id = str(
                            op_args.get("source_id")
                            or op_args.get("note_id")
                            or op_args.get("title")
                            or op_args.get("notebook_id")
                            or ""
                        )
                        if t_id:
                            labels.append(_resolve_kortex_label(t_id))
                    sync_sha, bytes_after = _hash_payload_snippet(
                        "\n".join(bulk_texts) if bulk_texts else None
                    )
                    sha256_after = sync_sha
                    tgt_suffix = f" targets={','.join(labels)}" if labels else ""
                    summary = f"bulk_mut_ops={len(mut_ops)}/{len(norm_ops)}{tgt_suffix}"
                else:
                    category = "READ_QUERY"
                    mutated = False
                    for _op_t, op_args in norm_ops:
                        for k_id in ("source_id", "notebook_id", "note_id"):
                            val_id = str(op_args.get(k_id) or "")
                            if val_id:
                                touched_uuids.append(val_id)
                    resp_obj = (
                        payload.get("toolResponse") or payload.get("response") or payload.get("result")
                    )
                    read_sha, bytes_after = _hash_payload_snippet(resp_obj)
                    sha256_after = read_sha
                    summary = f"bulk_read_ops={len(norm_ops)}"
            else:
                category = "VAULT_SYNC"
                mutated = True
                nb_raw = str(mcp_args.get("notebook_id") or "")
                src_raw = str(
                    mcp_args.get("source_id")
                    or mcp_args.get("note_id")
                    or mcp_args.get("title")
                    or ""
                )
                if nb_raw:
                    touched_uuids.append(nb_raw)
                if src_raw:
                    touched_uuids.append(src_raw)
                sync_body = (
                    mcp_args.get("content")
                    or mcp_args.get("text")
                    or mcp_args.get("note")
                    or mcp_args.get("highlight")
                    or mcp_args.get("new_title")
                )
                sync_sha, bytes_after = _hash_payload_snippet(sync_body)
                sha256_after = sync_sha
                nb_label = _resolve_kortex_label(nb_raw)
                src_label = _resolve_kortex_label(src_raw)
                summary = f"notebook={nb_label} target={src_label}".strip()
        else:
            category = "READ_QUERY"
            mutated = False
            nb_id = str(mcp_args.get("notebook_id") or "")
            src_id = str(mcp_args.get("source_id") or "")
            if nb_id:
                touched_uuids.append(nb_id)
            if src_id:
                touched_uuids.append(src_id)
            resp_obj = payload.get("toolResponse") or payload.get("response") or payload.get("result")
            read_sha, bytes_after = _hash_payload_snippet(resp_obj)
            sha256_after = read_sha
            if server == "kortex" and (nb_id or src_id):
                summary = f"mcp:{server}.{mcp_tool} ({_resolve_kortex_label(src_id or nb_id)})"
            else:
                summary = f"mcp:{server}.{mcp_tool}"

        if server == "kortex" and not error_str and touched_uuids:
            _update_epistemic_manifest(
                ts_iso=ts_iso,
                conv_id=conv_id,
                manifest_path=m_path,
                uuids=touched_uuids,
                category=category,
                tool_label=target,
                read_sha256=read_sha,
                sync_sha256=sync_sha,
                primary_vault_uuid=primary_vault_uuid,
            )
        elif post_tool_enricher is not None and not error_str:
            extra_mcp = post_tool_enricher(payload, category, target, summary, ts_iso, conv_id)
            if extra_mcp.get("category_override"):
                category = str(extra_mcp.pop("category_override"))
                mutated = bool(extra_mcp.pop("mutated_override", mutated))
            if extra_mcp.get("summary_override"):
                summary = str(extra_mcp.pop("summary_override"))
            if extra_mcp:
                _update_epistemic_manifest(
                    ts_iso=ts_iso,
                    conv_id=conv_id,
                    manifest_path=m_path,
                    category=category,
                    tool_label=target,
                    user_input_sha256=user_input_sha,
                    **extra_mcp,
                )
    elif tool_name == "invoke_subagent":
        sub_type = str(args.get("TypeName") or "")
        sub_role = str(args.get("Role") or "")
        sub_prompt = str(args.get("Prompt") or "")
        category = "SUBAGENT"
        target = sub_type
        summary = sub_role
        if not error_str:
            sub_comb = f"{sub_type} {sub_role} {sub_prompt}".lower()
            sub_taint = None
            if UNTRUSTED_INBOX_ID.lower() in sub_comb or "00_antigravity_inbox" in sub_comb:
                sub_taint = "SUBAGENT_UNTRUSTED_INBOX"
            if post_tool_enricher is not None:
                extra_sub = post_tool_enricher(payload, category, target, summary, ts_iso, conv_id)
                if extra_sub.get("taint_source"):
                    sub_taint = str(extra_sub["taint_source"])
            resp_obj = payload.get("toolResponse") or payload.get("response") or payload.get("result")
            sec_inc = (
                _inspect_response_for_injection(
                    ts_iso, conv_id, f"subagent:{sub_type}", resp_obj, check_stored_injection=bool(sub_taint)
                )
                if resp_obj
                else None
            )
            if sec_inc:
                sub_taint = "INJECTION_DETECTED_IN_RESPONSE"
            if sub_taint or sec_inc:
                _update_epistemic_manifest(
                    ts_iso=ts_iso,
                    conv_id=conv_id,
                    manifest_path=m_path,
                    category=category,
                    tool_label=f"subagent:{sub_type}",
                    user_input_sha256=user_input_sha,
                    taint_source=sub_taint,
                    security_incident=sec_inc,
                )

    try:
        git_head = git_head_short(ws_root)
        entry_core: dict[str, Any] = {
            "ts": ts_iso,
            "conv_id": conv_id,
            "step_idx": step_idx,
            "category": category,
            "tool": tool_name,
            "target": target,
            "summary": summary,
            "status": status,
            "sha256_before": sha256_before,
            "sha256_after": sha256_after,
            "bytes_after": bytes_after,
            "mutated": mutated,
            "git_head": git_head,
        }
        append_locked_audit_entry(a_log_path, entry_core)
    except Exception as exc:
        _log_hook_error("PostToolUse", exc, err_log_path=err_log_path)

    return {}


def check_session_start_anomalies(
    workspace_root: str = WORKSPACE_ROOT,
    now_dt: datetime | None = None,
) -> list[str]:
    """Workspace-neutral O(1) SessionStart check for security incidents and anomaly logs."""
    alerts: list[str] = []
    now_val = now_dt or now_local()
    cutoff_24h = now_val - timedelta(seconds=DANGLING_REPLY_TTL_SECONDS)

    m_data = _load_epistemic_manifest(os.path.join(workspace_root, "state/epistemic_manifest.json"))
    for sec_inc in (m_data.get("security_incidents") or [])[-5:]:
        if isinstance(sec_inc, dict) and not sec_inc.get("acknowledged"):
            try:
                inc_dt = datetime.fromisoformat(str(sec_inc.get("ts") or ""))
                if inc_dt >= cutoff_24h:
                    alerts.append(
                        f"Prompt-injection / hidden-Unicode payload detected in {sec_inc.get('source')} ({str(sec_inc.get('ts'))[:16]})."
                    )
            except ValueError:
                pass

    for err_rel in ("state/hook_errors.log", "state/audit_corrupt.jsonl"):
        err_p = os.path.join(workspace_root, err_rel)
        if os.path.isfile(err_p) and os.path.getsize(err_p) > 0:
            alerts.append(f"System anomaly log non-empty: {err_rel} ({os.path.getsize(err_p)}B).")

    return alerts


def audit_state_files(
    state_files_to_audit: list[tuple[Any, ...]],
    *,
    workspace_root: str,
    alert_prefix: str,
    extra_warnings: list[str] | None = None,
    include_anomalies: bool = True,
) -> dict[str, Any]:
    """Universal SessionStart state-file cap & staleness auditor across any workspace."""
    warnings: list[str] = []
    now_dt = now_local(workspace_root, use_showcase_anchor=True)
    today = now_dt.date()

    for spec in state_files_to_audit:
        label = spec[0]
        path = spec[1]
        max_lines = spec[2]
        max_bytes = spec[3] if len(spec) > 3 else 8000
        max_stale_days = spec[4] if len(spec) > 4 else None

        if not os.path.exists(path):
            warnings.append(f"Missing core state file: {label}")
            continue

        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
                content = "".join(lines)
            byte_size = len(content.encode("utf-8"))
        except OSError:
            continue

        if len(lines) > max_lines:
            warnings.append(
                f"{label} is at {len(lines)} lines (exceeds {max_lines}-line cap; compact immediately)."
            )
        if max_bytes and byte_size > max_bytes:
            warnings.append(
                f"{label} is at {byte_size} bytes (exceeds {max_bytes}-byte anti-bloat cap; compact immediately)."
            )

        match = re.search(
            r"(?:\*\*Last Updated:\*\*|\*\*Last Synced:\*\*|last_updated:)\s*(\d{4}-\d{2}-\d{2})",
            content,
            re.IGNORECASE,
        )
        if match:
            if max_stale_days is not None:
                try:
                    updated_date = datetime.strptime(match.group(1), "%Y-%m-%d").date()
                    delta_days = (today - updated_date).days
                    if delta_days > max_stale_days:
                        warnings.append(
                            f"{label} last_updated is {match.group(1)} ({delta_days} days old; confirm status with user)."
                        )
                except ValueError:
                    pass
        elif max_stale_days is not None:
            warnings.append(f"{label} is missing a visible Last Updated date.")

    if extra_warnings:
        warnings.extend(extra_warnings)
    if include_anomalies:
        try:
            warnings.extend(check_session_start_anomalies(workspace_root, now_dt=now_dt))
        except Exception as exc:
            _log_hook_error(
                "SessionStartAnomalies",
                exc,
                err_log_path=os.path.join(workspace_root, "state/hook_errors.log"),
            )

    if warnings:
        return {"injectSteps": [{"ephemeralMessage": f"{alert_prefix}: " + " | ".join(warnings)}]}
    return {}


def dispatch_hook_cli(
    *,
    workspace_root: str,
    pre_handler: Callable[[dict[str, Any]], dict[str, Any]],
    post_handler: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    session_handler: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    audit_log_path: str | None = None,
    cmd_redactor: Callable[[str], str] | None = None,
) -> None:
    """Standard single-process CLI dispatcher for PreToolUse, PostToolUse, and SessionStart."""
    event = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        raw_input = "" if sys.stdin.isatty() else sys.stdin.read()
        payload = json.loads(raw_input) if raw_input.strip() else {}
    except Exception:
        payload = {}

    a_log_path = audit_log_path or os.path.join(workspace_root, "state/audit_log.jsonl")
    err_log_path = os.path.join(workspace_root, "state/hook_errors.log")
    redact_fn = cmd_redactor or _redact_command

    if event == "PreToolUse":
        result = pre_handler(payload)
        if result.get("decision") == "deny":
            try:
                t_call = payload.get("toolCall") or {}
                t_name = str(t_call.get("name") or "unknown")
                t_args = t_call.get("args") or {}
                t_target = str(
                    t_args.get("Path")
                    or t_args.get("CommandLine")
                    or t_args.get("ToolName")
                    or t_name
                )[:120]
                append_locked_audit_entry(
                    a_log_path,
                    {
                        "ts": now_local().isoformat(timespec="seconds"),
                        "conv_id": str(payload.get("conversationId") or "local")[:12],
                        "step_idx": payload.get("stepIdx"),
                        "category": "PRE_TOOL_BLOCK",
                        "tool": t_name,
                        "target": redact_fn(t_target),
                        "summary": str(result.get("reason") or "")[:180],
                        "status": "BLOCKED",
                        "sha256_before": None,
                        "sha256_after": None,
                        "bytes_after": None,
                        "mutated": False,
                        "git_head": git_head_short(workspace_root),
                    },
                )
            except Exception as exc:
                _log_hook_error("PreToolBlockAudit", exc, err_log_path=err_log_path)
    elif event == "PostToolUse":
        if post_handler is not None:
            result = post_handler(payload)
        else:
            result = handle_post_tool_use(payload, workspace_root=workspace_root, audit_log_path=a_log_path)
    elif event == "SessionStart":
        result = session_handler(payload) if session_handler is not None else {}
    else:
        result = {}

    sys.stdout.write(json.dumps(result) + "\n")
