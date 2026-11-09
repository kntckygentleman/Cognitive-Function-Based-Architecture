#!/usr/bin/env python3
"""Claire-only orchestration lifecycle hooks (`claire-architect` workspace).

Universal security, anti-slop, CaMeL taint, Kortex token defaults, and SHA-256 hash-chained
audit logging live in `core_hooks.py`. This module layers strictly orchestrator-specific rules:
1. `[Claire Debug Mode]` lock on core identity, skill, workspace spec, and hook files.
2. Outbound Beeper SMS/RCS two-step human confirmation gate (`stage-reply` -> `send-staged`).
3. Shared Google Calendar optimistic concurrency gate (requiring secondary calendar reads).
4. Canonical NotebookLM Vault optimistic concurrency and UUID topology checks.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from core_hooks import (  # noqa: E402, F401
    ACCOUNTLESS_KORTEX_TOOLS,
    ALL_WORKSPACE_NAMES,
    AUDIT_LOG_PATH,
    BANNED_SLOP_TERMS_RE,
    BENIGN_REDIRECT_RE,
    COMPOUND_SHELL_OP_RE,
    CONTRAST_FRAME_RE,
    CORRUPT_LOG_PATH,
    DANGLING_REPLY_TTL_SECONDS,
    DEFAULT_KORTEX_ACCOUNT,
    ECOSYSTEM_REGISTRY_UUIDS,
    ECOSYSTEM_ROOT,
    EMAIL_REDACT_RE,
    EPISTEMIC_MANIFEST_PATH,
    EXFIL_AND_SLOPSQUAT_CMD_RE,
    EXOGENOUS_LOOKUP_TOOLS,
    GCAL_MUTATE_RE,
    GDRIVE_MUTATE_RE,
    GENESIS_HASH,
    HIDDEN_UNICODE_RE,
    HOOK_ERROR_LOG_PATH,
    ISO_DATETIME_RE,
    KORTEX_MUTATION_TOOLS,
    KORTEX_UUID_LABELS,
    LOCAL_TZ,
    MAX_AUDIT_LOG_BYTES,
    MAX_BAK_SEGMENTS,
    MAX_EXTERNAL_LOOKUPS,
    MAX_SECURITY_INCIDENTS,
    PHONE_REDACT_RE,
    PRE_HASH_CACHE_PATH,
    PSEUDO_WEIGHT_RE,
    QUICK_ADD_REDACT_RE,
    SECRET_FLAG_REDACT_RE,
    SELF_OBSERVER_ANCHORED_RE,
    STORED_INJECTION_RE,
    SUBSTANTIVE_DATE_STAMP_DELTA_BYTES,
    TIER_1_SPEC_DOMAINS,
    TIER_2_PRACTITIONER_DOMAINS,
    UNTRUSTED_INBOX_ID,
    UUID_EXTRACT_RE,
    VAULT_SOURCE_SPECS,
    WORKSPACE_ROOT,
    _atomic_stamp_state_cache_date,
    _classify_command as _core_classify_command,
    _classify_exogenous_lookup,
    _classify_file_target,
    _extract_file_mutation_text,
    _extract_verified_host,
    _hash_payload_snippet,
    _host_matches_domain_list,
    _inspect_response_for_injection,
    _is_pure_self_observer_command,
    _load_epistemic_manifest,
    _log_hook_error,
    _pop_pre_hash,
    _prune_old_bak_segments,
    _quarantine_corrupt_tail,
    _read_tail_hash_from_bak,
    _record_pre_hash,
    _redact_command as _core_redact_command,
    _resolve_default_workspace_root,
    _resolve_ecosystem_root,
    _resolve_kortex_label,
    _shell_targets_locked_path as _core_shell_targets_locked_path,
    _update_epistemic_manifest,
    _user_input_authorizes_command,
    append_locked_audit_entry,
    apply_kortex_defaults,
    audit_state_files,
    check_session_start_anomalies,
    dispatch_hook_cli,
    evaluate_base_pre_tool_use,
    file_sha256_and_size,
    get_workspace_state_files_to_audit,
    git_head_short,
    handle_post_tool_use as _core_handle_post_tool_use,
    lint_markdown_slop,
    lint_security_payload,
    normalize_kortex_bulk_ops,
    now_local,
    read_last_hash_from_fd,
    register_vault_source_specs,
)

PERSONAL_VAULT_UUID = os.environ.get(
    "CLAIRE_PERSONAL_VAULT_UUID", "00000000-0000-4000-8000-000000000100"
)
CLAIRE_VAULT_SOURCE_SPECS: dict[str, dict[str, Any]] = {
    PERSONAL_VAULT_UUID: {"label": "Orchestrator_Architecture_Vault", "volatility": "CONTAINER", "ttl_days": None, "parent": None, "owner": "claire-architect"},
    "00000000-0000-4000-8000-000000000101": {"label": "01_Operator_And_Counterweight_Spec.md", "volatility": "STRUCTURAL_IDENTITY", "ttl_days": 90, "parent": PERSONAL_VAULT_UUID, "owner": "claire-architect"},
    "00000000-0000-4000-8000-000000000102": {"label": "02_Active_Operations_And_Sprint_Protocols.md", "volatility": "OPERATIONAL_SPRINT", "ttl_days": 7, "parent": PERSONAL_VAULT_UUID, "owner": "claire-architect"},
    "00000000-0000-4000-8000-000000000103": {"label": "03_Shared_Calendar_And_Comms_Governance.md", "volatility": "STRUCTURAL_GOVERNANCE", "ttl_days": 90, "parent": PERSONAL_VAULT_UUID, "owner": "claire-architect"},
    "00000000-0000-4000-8000-000000000104": {"label": "04_Chronological_Changelog_Directives.md", "volatility": "ARCHITECTURAL_CHANGELOG", "ttl_days": 30, "parent": PERSONAL_VAULT_UUID, "owner": "claire-architect"},
    "00000000-0000-4000-8000-000000000200": {"label": "Media_Storyboard_Vault", "volatility": "DEFERRED_DORMANT", "ttl_days": None, "parent": None, "owner": "claire-architect"},
    "00000000-0000-4000-8000-000000000201": {"label": "00_STATE_Media_Production_Spec.md", "volatility": "DEFERRED_DORMANT", "ttl_days": None, "parent": "00000000-0000-4000-8000-000000000200", "owner": "claire-architect"},
}
register_vault_source_specs(CLAIRE_VAULT_SOURCE_SPECS)
CLAIRE_ACTIVE_TTL_SOURCES = tuple(
    uid for uid, spec in CLAIRE_VAULT_SOURCE_SPECS.items() if spec["ttl_days"] is not None
)
APPROVAL_ID_RE = re.compile(r"\b(RPL-[A-Za-z0-9]{6,12})\b")
CALENDAR_ARG_RE = re.compile(r"(?:--calendar|--cal|-c)\s*(?:=|\s+)\s*['\"]?([^\s'\"]+)", re.IGNORECASE)
CHAT_ID_ARG_RE = re.compile(r"(?:--chat-id|--chat)\s*(?:=|\s+)\s*['\"]?([^\s'\"]+)", re.IGNORECASE)
REMIND_ARG_RE = re.compile(
    r"(?:--remind-at|--when|--at|--minutes|--hours)\s*(?:=|\s+)\s*['\"]?([^\s'\"]+)",
    re.IGNORECASE,
)

LOCKED_REL_PATHS = (
    "SYSTEM_PROMPT.md",
    "GEMINI.md",
    ".agents/rules/claire-state-hydration.md",
    ".agents/rules/human-voice.md",
    ".agents/hooks.json",
    ".agents/scripts/core_hooks.py",
    ".agents/scripts/claire_hooks.py",
    ".agents/scripts/audit_log.py",
    ".agents/skills/agent-scaffolder/SKILL.md",
    ".agents/skills/agent-scaffolder/scripts/scaffold_agent.py",
    ".agents/skills/council/SKILL.md",
    ".agents/skills/council/references/personas.md",
    ".agents/skills/beeper-sms-triage/SKILL.md",
    ".agents/skills/beeper-sms-triage/scripts/beeper_triage.py",
    ".agents/skills/iterate/SKILL.md",
    ".agents/skills/iterate/references/critic_panels.md",
    ".agents/skills/iterate/scripts/iterate_stage.py",
    "workspaces/01_claire_architect_INTJ.md",
    "workspaces/02_grant_quant_INTP.md",
    "workspaces/03_chad_biometrics_ISTP.md",
    "workspaces/04_andy_studio_ESTP.md",
    "workspaces/05_elmer_tabletop_ISTJ.md",
)

LOCKED_PATHS = {
    os.path.realpath(os.path.join(WORKSPACE_ROOT, rel)) for rel in LOCKED_REL_PATHS
}

STATE_FILES_TO_AUDIT = [
    ("active_sprint.md", os.path.join(WORKSPACE_ROOT, "state/active_sprint.md"), 100, 8000, 14),
    ("ecosystem_registry.md", os.path.join(WORKSPACE_ROOT, "state/ecosystem_registry.md"), 100, 8000, None),
    ("routing_matrix.md", os.path.join(WORKSPACE_ROOT, "state/routing_matrix.md"), 100, 8000, None),
    ("archive_decisions.md", os.path.join(WORKSPACE_ROOT, "state/archive_decisions.md"), 150, 16000, None),
]

SMS_AFFIRMATIVE_CONFIRM_RE = re.compile(
    r"\b(?:yes|yep|yeah|send|approve|approved|ship|go\s+ahead|do\s+it|ok|okay)\b",
    re.IGNORECASE,
)
SMS_NEGATIVE_CONFIRM_RE = re.compile(
    r"\b(?:no|not|don'?t|wait|hold|cancel|change|edit|instead|stop|drop)\b",
    re.IGNORECASE,
)


SHARED_CAL_ID = os.environ.get(
    "CLAIRE_SHARED_CALENDAR_ID", "shared-team@group.calendar.google.com"
)
PRIMARY_CAL_ID = os.environ.get("CLAIRE_PRIMARY_CALENDAR_ID", "operator@example.com")
SECONDARY_CAL_IDS = tuple(
    x.strip()
    for x in os.environ.get(
        "CLAIRE_SECONDARY_CALENDAR_IDS", "secondary_work_cal,secondary_appts_cal,secondary_events_cal"
    ).split(",")
    if x.strip()
)


def _redact_command(cmd: str) -> str:
    cleaned = re.sub(
        re.escape(SHARED_CAL_ID),
        "[CAL:Shared]",
        cmd.strip(),
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        re.escape(PRIMARY_CAL_ID),
        "[CAL:Primary]",
        cleaned,
        flags=re.IGNORECASE,
    )
    return _core_redact_command(cleaned)


def _extract_command_context_tags(cmd: str) -> str:
    tags: list[str] = []
    rpl_match = APPROVAL_ID_RE.search(cmd)
    if rpl_match:
        tags.append(rpl_match.group(1))
    lower = cmd.lower()
    if "gcalendar" in lower:
        cal_match = CALENDAR_ARG_RE.search(cmd)
        if cal_match:
            cal_raw = cal_match.group(1)
            cal_label = (
                "Shared"
                if "shared" in cal_raw.lower() or SHARED_CAL_ID.lower() in cal_raw.lower()
                else ("Primary" if PRIMARY_CAL_ID.lower() in cal_raw.lower() or "primary" in cal_raw.lower() else cal_raw[:12])
            )
            tags.append(f"cal={cal_label}")
        dt_match = ISO_DATETIME_RE.search(cmd)
        if dt_match:
            tags.append(f"when={dt_match.group(1)}")
    if "beeper_triage.py" in lower:
        cid_match = CHAT_ID_ARG_RE.search(cmd)
        if cid_match:
            raw_cid = cid_match.group(1)
            tags.append(f"chat={raw_cid[-6:]}")
        rem_match = REMIND_ARG_RE.search(cmd)
        if rem_match:
            tags.append(f"when={rem_match.group(1)[:16]}")
        elif ISO_DATETIME_RE.search(cmd):
            tags.append(f"when={ISO_DATETIME_RE.search(cmd).group(1)}")  # type: ignore[union-attr]
    return f" [{' '.join(tags)}]" if tags else ""


def _classify_command(cmd: str) -> str:
    lower = cmd.lower()
    if "beeper_triage.py" in lower:
        if "stage-reply" in lower:
            return "STAGE_DRAFT"
        if "send-staged" in lower or "remind" in lower:
            return "EXTERNAL_ACTION"
        return "READ_QUERY"
    return _core_classify_command(cmd)


def _shell_targets_locked_path(cmd: str) -> str | None:
    return _core_shell_targets_locked_path(cmd, LOCKED_REL_PATHS)


def _is_canonical_state_title(title: str) -> bool:
    return bool(re.match(r"^0[0-4]_", title.strip()))


def _gcal_targets_shared(cmd: str) -> bool:
    low = cmd.lower()
    return SHARED_CAL_ID.lower() in low or bool(
        re.search(r"(?:--calendar|--cal|-c)\s*(?:=|\s+)\s*['\"]?shared\b", low)
    )


def _gcal_reads_secondary(cmd: str) -> bool:
    low = cmd.lower()
    if re.search(r"\b(?:freebusy|conflicts)\b", low):
        return True
    if any(cid.lower() in low for cid in SECONDARY_CAL_IDS):
        return True
    return bool(
        re.search(
            r"(?:--calendar|--cal|-c)\s*(?:=|\s+)\s*['\"]?(?:work|appointments|events|secondary)\b",
            low,
        )
    )


def _user_input_confirms_single_staged_sms(user_input: str) -> bool:
    if not user_input or SMS_NEGATIVE_CONFIRM_RE.search(user_input):
        return False
    return bool(SMS_AFFIRMATIVE_CONFIRM_RE.search(user_input))


def _is_staged_reply_active(rec: Any, now_dt: datetime) -> bool:
    if not isinstance(rec, dict) or rec.get("status") != "STAGED":
        return False
    ts_str = str(rec.get("ts") or rec.get("staged_at") or "")
    if not ts_str:
        return True
    try:
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=now_dt.tzinfo)
        return (now_dt - dt).total_seconds() <= DANGLING_REPLY_TTL_SECONDS
    except ValueError:
        return True


def _record_manifest_user_input_sha(user_input_sha: str) -> None:
    try:
        os.makedirs(os.path.dirname(EPISTEMIC_MANIFEST_PATH), exist_ok=True)
        with open(EPISTEMIC_MANIFEST_PATH, "a+", encoding="utf-8") as mf:
            fcntl.flock(mf.fileno(), fcntl.LOCK_EX)
            mf.seek(0)
            raw = mf.read().strip()
            data = json.loads(raw) if raw else {}
            if not isinstance(data, dict):
                data = {}
            data["last_user_input_sha256"] = user_input_sha
            mf.seek(0)
            mf.truncate()
            mf.write(json.dumps(data, indent=2) + "\n")
            mf.flush()
    except Exception:
        pass


def _is_uuid_recently_verified(
    uid: str, read_uuids: set[str], vault_sources: dict[str, Any], now_dt: datetime, ttl_minutes: int = 30
) -> bool:
    if not uid:
        return False
    if uid in read_uuids:
        return True
    rec = vault_sources.get(uid)
    if isinstance(rec, dict):
        last_ts = str(rec.get("last_read_ts") or rec.get("last_sync_ts") or "")
        if last_ts:
            try:
                dt = datetime.fromisoformat(last_ts)
                if (now_dt - dt).total_seconds() <= ttl_minutes * 60:
                    return True
            except ValueError:
                pass
    return False


def _enrich_claire_post_tool(
    payload: dict[str, Any],
    category: str,
    target: str,
    summary: str,
    ts_iso: str,
    conv_id: str,
) -> dict[str, Any]:
    tool_call = payload.get("toolCall") or {}
    tool_name = tool_call.get("name", "")
    args = tool_call.get("args") or {}
    resp_obj = payload.get("toolResponse") or payload.get("response") or payload.get("result")
    out: dict[str, Any] = {}

    if tool_name == "run_command":
        cmd_line = str(args.get("CommandLine") or "")
        low_cmd = cmd_line.lower()
        try:
            resp_str = (
                resp_obj
                if isinstance(resp_obj, str)
                else json.dumps(resp_obj, ensure_ascii=False)
                if resp_obj is not None
                else ""
            )
        except Exception:
            resp_str = ""
        if category == "READ_QUERY" and "gcalendar" in low_cmd:
            out["gcal_scope_read"] = (
                "secondary_checked" if _gcal_reads_secondary(cmd_line) else "primary_only"
            )
        if category == "READ_QUERY" and "beeper_triage.py" in low_cmd:
            out["taint_source"] = "BEEPER_INBOUND_SMS"
        rpl_ids = APPROVAL_ID_RE.findall(f"{cmd_line} {summary} {resp_str}")
        if rpl_ids and category == "STAGE_DRAFT":
            rpl_stg = rpl_ids[0]
            out["rpl_staged"] = rpl_stg
            if rpl_stg not in summary:
                out["summary_override"] = f"{summary} [rpl={rpl_stg}]".strip()
        elif rpl_ids and category == "EXTERNAL_ACTION" and "send-staged" in low_cmd:
            out["rpl_sent"] = rpl_ids[0]
    elif tool_name == "call_mcp_tool" and args.get("ServerName") == "beeper":
        mcp_tool = str(args.get("ToolName") or "")
        mcp_args = args.get("Arguments") or {}
        if mcp_tool in ("set_chat_reminder", "clear_chat_reminder", "archive_chat"):
            raw_cid = str(mcp_args.get("chatID") or mcp_args.get("chat_id") or "")
            cid_tag = f" chat={raw_cid[-6:]}" if raw_cid else ""
            rem_obj = mcp_args.get("reminder") or {}
            rem_ms = (
                rem_obj.get("remindAtMs") if isinstance(rem_obj, dict) else mcp_args.get("remindAtMs")
            )
            rem_tag = f" remindAtMs={rem_ms}" if rem_ms else ""
            out["category_override"] = "EXTERNAL_ACTION"
            out["mutated_override"] = True
            out["summary_override"] = f"action={mcp_tool}{cid_tag}{rem_tag}"
        elif category == "READ_QUERY":
            sec_inc = _inspect_response_for_injection(ts_iso, conv_id, target, resp_obj)
            out["taint_source"] = "INJECTION_DETECTED_IN_RESPONSE" if sec_inc else "BEEPER_INBOUND_SMS"
            if sec_inc:
                out["security_incident"] = sec_inc
    elif tool_name == "invoke_subagent":
        sub_comb = f"{args.get('TypeName', '')} {args.get('Role', '')} {args.get('Prompt', '')}".lower()
        if "beeper" in sub_comb or "sms" in sub_comb:
            out["taint_source"] = "SUBAGENT_BEEPER_SMS"

    return out


def handle_pre_tool_use(payload: dict[str, Any]) -> dict[str, Any]:
    base_res = evaluate_base_pre_tool_use(
        payload,
        workspace_root=WORKSPACE_ROOT,
        locked_paths=LOCKED_PATHS,
        locked_rel_paths=LOCKED_REL_PATHS,
        debug_phrase="[Claire Debug Mode]",
    )
    if base_res.get("decision") == "deny":
        return base_res

    tool_call = payload.get("toolCall") or {}
    tool_name = tool_call.get("name", "")
    args = tool_call.get("args") or {}
    last_user_input = (payload.get("lastUserInput") or "").lstrip()
    user_input_sha = (
        hashlib.sha256(last_user_input.encode("utf-8")).hexdigest()[:16] if last_user_input else "empty"
    )
    conv_id = str(payload.get("conversationId") or "local")[:12]

    if tool_name == "run_command":
        cmd_line = str(args.get("CommandLine") or "")
        beeper_m = re.search(
            r"(?:^|[;&|]\s*|\s)(?:python3?(?:\.\d+)?\s+)?(?:\S*/)?beeper_triage\.py\s+(stage-reply|send-staged)\b",
            cmd_line,
            re.IGNORECASE,
        )
        beeper_subcmd = beeper_m.group(1).lower() if beeper_m else ""
        if beeper_subcmd == "stage-reply":
            _record_manifest_user_input_sha(user_input_sha)
        elif beeper_subcmd == "send-staged":
            rpl_m = APPROVAL_ID_RE.search(cmd_line)
            if not rpl_m:
                return {
                    "decision": "deny",
                    "reason": "Outbound SMS Gate: 'send-staged' requires an explicit approval_id (RPL-xxxxxx).",
                }
            rpl_id = rpl_m.group(1)
            m_data = _load_epistemic_manifest(EPISTEMIC_MANIFEST_PATH)
            staged_map = m_data.get("staged_replies") or {}
            rpl_rec = staged_map.get(rpl_id) if isinstance(staged_map, dict) else None
            if not isinstance(rpl_rec, dict) or rpl_rec.get("status") != "STAGED":
                return {
                    "decision": "deny",
                    "reason": (
                        f"Outbound SMS Gate: Approval ID '{rpl_id}' is not in STAGED state in "
                        "epistemic_manifest.json. Stage the draft first via 'stage-reply'."
                    ),
                }
            staged_input_sha = rpl_rec.get("user_input_sha256")
            if staged_input_sha and staged_input_sha == user_input_sha:
                return {
                    "decision": "deny",
                    "reason": (
                        f"Outbound SMS Human-Turn Gate: Cannot run 'stage-reply' and 'send-staged' "
                        f"({rpl_id}) in the same turn. Present {rpl_id} to the user and wait for "
                        "their confirmation turn."
                    ),
                }
            now_dt = now_local()
            pending_count = sum(
                1
                for v in (staged_map.values() if isinstance(staged_map, dict) else ())
                if _is_staged_reply_active(v, now_dt)
            )
            authorized = _user_input_authorizes_command(last_user_input, rpl_id) or (
                pending_count == 1 and _user_input_confirms_single_staged_sms(last_user_input)
            )
            if not authorized:
                return {
                    "decision": "deny",
                    "reason": (
                        f"Outbound SMS Approval Gate: 'send-staged' requires user confirmation "
                        f"(either referencing '{rpl_id}' or an affirmative confirmation when exactly "
                        "one draft is pending)."
                    ),
                }

        if "gcalendar" in cmd_line.lower() and GCAL_MUTATE_RE.search(cmd_line) and _gcal_targets_shared(cmd_line):
            m_data = _load_epistemic_manifest(EPISTEMIC_MANIFEST_PATH)
            conv_scopes = ((m_data.get("conv_reads") or {}).get(conv_id) or {}).get("gcal_scopes") or []
            if "secondary_checked" not in conv_scopes:
                return {
                    "decision": "deny",
                    "reason": (
                        "Optimistic Concurrency Gate (Shared Calendar): Read secondary "
                        "calendars ('Work', 'Appointments', 'Events', or 'freebusy') in this "
                        "conversation before mutating the shared calendar."
                    ),
                }

    if tool_name == "call_mcp_tool" and args.get("ServerName") == "beeper":
        if args.get("ToolName") == "send_message":
            return {
                "decision": "deny",
                "reason": (
                    "Outbound SMS/RCS Gate: Direct Beeper MCP 'send_message' calls are blocked. "
                    "Stage the draft first via 'beeper_triage.py stage-reply' to generate an "
                    "approval_id (RPL-xxxxxx) and transmit only via 'send-staged' after user confirmation."
                ),
            }

    if tool_name == "call_mcp_tool" and args.get("ServerName") == "kortex":
        kortex_tool = args.get("ToolName", "")
        tool_args = dict(args.get("Arguments") or {})
        occ_tools = ("update_notebooklm_note", "set_source_note", "rename_source", "rename_notebook", "add_text_source")
        if kortex_tool in (*occ_tools, "bulk"):
            ops_to_check: list[tuple[str, dict[str, Any]]] = []
            if kortex_tool == "bulk":
                for op_t, op_a in normalize_kortex_bulk_ops(tool_args):
                    if op_t in occ_tools:
                        ops_to_check.append((op_t, op_a))
            else:
                ops_to_check.append((kortex_tool, tool_args))

            m_data: dict[str, Any] | None = None
            now_dt = now_local()
            for op_t, op_a in ops_to_check:
                nb_id = str(op_a.get("notebook_id") or "").strip()
                src_id = str(op_a.get("source_id") or op_a.get("note_id") or "").strip()
                title_str = str(op_a.get("title") or "").strip()
                requires_occ = op_t != "add_text_source" or _is_canonical_state_title(title_str)
                if requires_occ and (nb_id in KORTEX_UUID_LABELS or src_id in KORTEX_UUID_LABELS):
                    if m_data is None:
                        m_data = _load_epistemic_manifest(EPISTEMIC_MANIFEST_PATH)
                    read_uuids = set(
                        ((m_data.get("conv_reads") or {}).get(conv_id) or {}).get("kortex_uuids") or []
                    )
                    v_sources = m_data.get("vault_sources") or {}
                    nb_ok = _is_uuid_recently_verified(nb_id, read_uuids, v_sources, now_dt)
                    src_ok = _is_uuid_recently_verified(src_id, read_uuids, v_sources, now_dt)
                    if not nb_ok and not src_ok:
                        return {
                            "decision": "deny",
                            "reason": (
                                "Optimistic Concurrency Gate (Vault SSoT): Read the target notebook or "
                                "canonical source ('get_source_content' or 'list_sources') in this "
                                "conversation before overwriting or mutating canonical Vault state."
                            ),
                        }

    return base_res


def handle_post_tool_use(payload: dict[str, Any]) -> dict[str, Any]:
    return _core_handle_post_tool_use(
        payload,
        workspace_root=WORKSPACE_ROOT,
        audit_log_path=AUDIT_LOG_PATH,
        manifest_path=EPISTEMIC_MANIFEST_PATH,
        pre_hash_path=PRE_HASH_CACHE_PATH,
        cmd_redactor=_redact_command,
        cmd_classifier=_classify_command,
        cmd_tag_extractor=_extract_command_context_tags,
        post_tool_enricher=_enrich_claire_post_tool,
        primary_vault_uuid=PERSONAL_VAULT_UUID,
    )


def _check_session_start_epistemic_o1(
    workspace_root: str = WORKSPACE_ROOT,
    now_dt: datetime | None = None,
) -> list[str]:
    alerts: list[str] = []
    now_val = now_dt or now_local()
    cutoff_24h = now_val - timedelta(seconds=DANGLING_REPLY_TTL_SECONDS)

    reg_path = os.path.join(workspace_root, "state/ecosystem_registry.md")
    if os.path.isfile(reg_path):
        try:
            with open(reg_path, "r", encoding="utf-8") as rf:
                reg_uuids = {u.lower() for u in UUID_EXTRACT_RE.findall(rf.read())}
            hook_uuids = set(ECOSYSTEM_REGISTRY_UUIDS)
            if reg_uuids != hook_uuids:
                alerts.append(
                    f"Topology UUID mismatch (missing_in_hooks={sorted(reg_uuids - hook_uuids)}, "
                    f"missing_in_registry={sorted(hook_uuids - reg_uuids)})."
                )
        except OSError:
            pass

    m_data = _load_epistemic_manifest(os.path.join(workspace_root, "state/epistemic_manifest.json"))
    rpl_map = m_data.get("staged_replies") or {}
    if isinstance(rpl_map, dict):
        for rpl_id, r_info in sorted(rpl_map.items()):
            if not isinstance(r_info, dict) or r_info.get("status") != "STAGED":
                continue
            try:
                staged_dt = datetime.fromisoformat(str(r_info.get("ts") or ""))
                if staged_dt >= cutoff_24h:
                    alerts.append(f"Unsent staged SMS draft (<24h): {rpl_id} ({str(r_info.get('ts'))[:16]}).")
            except ValueError:
                pass

    alerts.extend(check_session_start_anomalies(workspace_root, now_dt=now_val))
    return alerts


def handle_session_start(_payload: dict[str, Any]) -> dict[str, Any]:
    now_dt = now_local(WORKSPACE_ROOT, use_showcase_anchor=True)
    extra = _check_session_start_epistemic_o1(WORKSPACE_ROOT, now_dt=now_dt)
    return audit_state_files(
        STATE_FILES_TO_AUDIT,
        workspace_root=WORKSPACE_ROOT,
        alert_prefix="STATE AUDIT ALERT",
        extra_warnings=extra,
        include_anomalies=False,
    )


def main() -> None:
    dispatch_hook_cli(
        workspace_root=WORKSPACE_ROOT,
        pre_handler=handle_pre_tool_use,
        post_handler=handle_post_tool_use,
        session_handler=handle_session_start,
        audit_log_path=AUDIT_LOG_PATH,
        cmd_redactor=_redact_command,
    )


if __name__ == "__main__":
    main()
