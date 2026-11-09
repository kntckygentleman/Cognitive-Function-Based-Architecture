#!/usr/bin/env python3
"""Zero-Token Provenance Ledger Query, Chain Verifier, Dynamic Changelog, & ADR CLI.

Imports shared hashing, locking, configurable timezone (default UTC), .git/HEAD resolution, and
anti-slop linting primitives directly from core_hooks.py (Single Source of Truth).

Interfaces with:
1. state/audit_log.jsonl (+ rotated .bak segments, fcntl.flock-serialized, SHA-256 hash-chained)
2. state/archive_decisions.md (compact ADR-XXXX index for strategic decisions)

Subcommands:
- query: Filter and inspect exact timestamps, targets, categories, pre/post SHA-256 hashes,
  and outcomes from state/audit_log.jsonl (hiding READ_QUERY, ARTIFACT_STAGE, and no-op writes
  by default unless --include-reads is passed, and supporting --all-workspaces / -a).
- summary: Aggregate signal tool counts, mutated CODE_SYSTEM and STATE_CACHE files,
  VAULT_SYNC targets, and EXTERNAL_ACTION commitments over a rolling window (--days, default 14,
  supporting --all-workspaces / -a).
- changelog: Render a grouped Keep-a-Changelog view (or JSON via --format json) on the fly
  directly from CODE_SYSTEM and ADR_DECISION events in state/audit_log.jsonl, collapsing
  repeated daily edits per file into a single net sha256 span.
- verify: Validate the RFC-8785 / SHA-256 cryptographic hash chain across rotated .bak segments
  and state/audit_log.jsonl (single workspace or --all-workspaces / -a).
- record-adr: Validate against lint_markdown_slop, append a sequentially numbered ADR-XXXX
  decision record to state/archive_decisions.md under fcntl.flock, and log a single linked
  ADR_DECISION event in state/audit_log.jsonl.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import fcntl
import glob
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from core_hooks import (  # noqa: E402
    ALL_WORKSPACE_NAMES,
    AUDIT_LOG_PATH as DEFAULT_AUDIT_LOG,
    DANGLING_REPLY_TTL_SECONDS,
    ECOSYSTEM_REGISTRY_UUIDS,
    ECOSYSTEM_ROOT,
    GENESIS_HASH,
    HIDDEN_UNICODE_RE,
    KORTEX_UUID_LABELS,
    LOCAL_TZ,
    UUID_EXTRACT_RE,
    VAULT_SOURCE_SPECS,
    WORKSPACE_ROOT,
    _load_epistemic_manifest,
    append_locked_audit_entry,
    file_sha256_and_size,
    get_workspace_state_files_to_audit,
    git_head_short,
    lint_markdown_slop,
    lint_security_payload,
    now_local,
    validate_domain_vault_manifests,
    validate_persona_and_panel_congruence,
)

_RAW_WS_NAME = os.path.basename(WORKSPACE_ROOT.rstrip("/"))
CURRENT_WORKSPACE_NAME = _RAW_WS_NAME if _RAW_WS_NAME in ALL_WORKSPACE_NAMES else "claire-architect"
DEFAULT_ADR_FILE = os.path.join(WORKSPACE_ROOT, "state/archive_decisions.md")

WORKSPACE_TURN0_SPECS = (
    (
        "Claire",
        "claire-architect",
        "SYSTEM_PROMPT.md",
        "workspaces/01_claire_architect_INTJ.md",
        ".agents/rules/human-voice.md",
        ("state/ecosystem_registry.md", "state/routing_matrix.md", "state/active_sprint.md"),
        7648,
    ),
    (
        "Elmer",
        "elmer",
        "ELMER_PROMPT.md",
        "workspaces/05_elmer_tabletop_ISTJ.md",
        ".agents/rules/human-voice.md",
        (
            "state/campaign_primary_summary.md",
            "state/campaign_primary_roster.md",
            "state/campaign_secondary_summary.md",
        ),
        14504,
    ),
    (
        "Chad",
        "chad",
        "CHAD_PROMPT.md",
        "workspaces/03_chad_biometrics_ISTP.md",
        ".agents/rules/human-voice.md",
        (
            "state/active_protocol_and_labs.md",
            "state/training_program_state.md",
        ),
        6412,
    ),
    (
        "Andy",
        "andy",
        "ANDY_PROMPT.md",
        "workspaces/04_andy_studio_ESTP.md",
        ".agents/rules/human-voice.md",
        ("state/style_and_catalog_dna.md", "state/active_setlist.md"),
        5650,
    ),
    (
        "Grant",
        "grant",
        "GRANT_PROMPT.md",
        "workspaces/02_grant_quant_INTP.md",
        ".agents/rules/human-voice.md",
        ("state/strategy_lab_and_models.md", "state/paper_portfolio_and_ledger.md"),
        4361,
    ),
)


def _resolve_workspace_dir(ws_name: str, workspace_root: str = WORKSPACE_ROOT, ecosystem_root: str = ECOSYSTEM_ROOT) -> str:
    if ws_name == CURRENT_WORKSPACE_NAME:
        return workspace_root
    cand = os.path.join(ecosystem_root, ws_name)
    return cand


def _active_workspace_names(workspace_root: str = WORKSPACE_ROOT, ecosystem_root: str = ECOSYSTEM_ROOT) -> tuple[str, ...]:
    if ecosystem_root == workspace_root and not os.path.isdir(os.path.join(ecosystem_root, "elmer")):
        return (CURRENT_WORKSPACE_NAME,)
    return ALL_WORKSPACE_NAMES


def _get_ordered_log_segments(base_log_path: str, include_archives: bool = True) -> list[str]:
    segments: list[str] = []
    if include_archives:
        segments.extend(sorted(glob.glob(f"{base_log_path}.*.bak")))
    if os.path.isfile(base_log_path):
        segments.append(base_log_path)
    return segments


def _active_log_needs_archives(base_log_path: str, min_date: str | None) -> bool:
    if not min_date:
        return False
    if not os.path.isfile(base_log_path) or os.path.getsize(base_log_path) == 0:
        return True
    try:
        with open(base_log_path, "r", encoding="utf-8") as f:
            for line in f:
                raw = line.strip()
                if not raw:
                    continue
                obj = json.loads(raw)
                first_date = str(obj.get("ts") or "")[:10]
                return bool(first_date and first_date > min_date)
    except Exception:
        return True
    return False


def _read_entries_strict(
    base_log_path: str,
    workspace_label: str = CURRENT_WORKSPACE_NAME,
    include_archives: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    parse_errors: list[dict[str, Any]] = []
    for seg_path in _get_ordered_log_segments(base_log_path, include_archives=include_archives):
        seg_name = os.path.basename(seg_path)
        try:
            with open(seg_path, "r", encoding="utf-8") as f:
                for idx, line in enumerate(f, start=1):
                    raw = line.strip()
                    if not raw:
                        continue
                    try:
                        obj = json.loads(raw)
                        obj["_line"] = idx
                        obj["_segment"] = seg_name
                        obj["_workspace"] = workspace_label
                        entries.append(obj)
                    except json.JSONDecodeError as exc:
                        parse_errors.append(
                            {
                                "workspace": workspace_label,
                                "segment": seg_name,
                                "line": idx,
                                "error": f"JSONDecodeError: {exc}",
                            }
                        )
        except OSError:
            continue
    return entries, parse_errors


def _collect_entries_for_args(
    args: argparse.Namespace, min_date: str | None = None
) -> list[dict[str, Any]]:
    force_archives = bool(getattr(args, "include_archives", False))
    if getattr(args, "all_workspaces", False):
        combined: list[dict[str, Any]] = []
        for ws in _active_workspace_names():
            ws_dir = _resolve_workspace_dir(ws)
            ws_log = os.path.join(ws_dir, "state/audit_log.jsonl")
            use_arch = force_archives or _active_log_needs_archives(ws_log, min_date)
            ents, _ = _read_entries_strict(ws_log, workspace_label=ws, include_archives=use_arch)
            combined.extend(ents)
        combined.sort(key=lambda x: str(x.get("ts") or ""))
        return combined
    ws_label = CURRENT_WORKSPACE_NAME
    use_arch = force_archives or _active_log_needs_archives(args.log_file, min_date)
    ents, _ = _read_entries_strict(
        args.log_file, workspace_label=ws_label, include_archives=use_arch
    )
    return ents


def _format_workspace_relpath(target_path: str, ws_label: str, all_workspaces: bool) -> str:
    ws_root = _resolve_workspace_dir(ws_label)
    rel = target_path
    if os.path.isabs(target_path):
        for candidate_root in (ws_root, WORKSPACE_ROOT):
            try:
                cand_rel = os.path.relpath(target_path, candidate_root)
                if not cand_rel.startswith(".."):
                    rel = cand_rel
                    break
            except ValueError:
                pass
    return f"[{ws_label}]/{rel}" if all_workspaces else rel


def _is_signal_entry(e: dict[str, Any]) -> bool:
    cat = str(e.get("category") or "")
    if cat in ("READ_QUERY", "ARTIFACT_STAGE"):
        return False
    if e.get("mutated") is False and str(e.get("status") or "") == "OK":
        return False
    return True


def cmd_query(args: argparse.Namespace) -> None:
    entries = _collect_entries_for_args(args, min_date=args.since)
    filtered: list[dict[str, Any]] = []

    for e in entries:
        ts = str(e.get("ts") or "")
        date_prefix = ts[:10]
        cat = str(e.get("category") or "")

        if not args.include_reads and not args.category:
            if not _is_signal_entry(e):
                continue

        if args.since and date_prefix < args.since:
            continue
        if args.until and date_prefix > args.until:
            continue
        if args.category and args.category.upper() != "ALL":
            if cat.upper() != args.category.upper():
                continue
        if args.target:
            hay = f"{e.get('target', '')} {e.get('summary', '')}".lower()
            if args.target.lower() not in hay:
                continue
        if args.status:
            if not str(e.get("status") or "").upper().startswith(args.status.upper()):
                continue
        filtered.append(e)

    if args.limit and args.limit > 0:
        filtered = filtered[-args.limit :]

    if args.format == "json":
        clean = [
            {
                **({} if not args.all_workspaces else {"workspace": item.get("_workspace")}),
                **{k: v for k, v in item.items() if not k.startswith("_")},
            }
            for item in filtered
        ]
        print(json.dumps(clean, indent=2))
        return

    if not filtered:
        print("No matching audit log entries found.")
        return

    print(
        f"{'TIMESTAMP':<25} | {'CATEGORY':<17} | {'TOOL':<14} | {'STATUS':<8} | {'GIT':<7} | {'TARGET / SUMMARY'}"
    )
    print("-" * 114)
    for e in filtered:
        ts = str(e.get("ts") or "")[:25]
        cat = str(e.get("category") or "")[:17]
        tool = str(e.get("tool") or "")[:14]
        stat = str(e.get("status") or "")[:8]
        git_h = str(e.get("git_head") or "")[:7]
        ws_label = str(e.get("_workspace") or CURRENT_WORKSPACE_NAME)
        tgt = _format_workspace_relpath(str(e.get("target") or ""), ws_label, bool(args.all_workspaces))
        summ = str(e.get("summary") or "")
        sha_b = e.get("sha256_before")
        sha_a = e.get("sha256_after")
        if sha_b and sha_a:
            sha_tag = f" [sha256:{sha_b[:8]}->{sha_a[:8]}]"
        elif sha_a:
            sha_tag = f" [sha256:{sha_a[:8]}]"
        else:
            sha_tag = ""
        detail = f"{tgt} ({summ}){sha_tag}" if summ else f"{tgt}{sha_tag}"
        print(f"{ts:<25} | {cat:<17} | {tool:<14} | {stat:<8} | {git_h:<7} | {detail}")


def cmd_summary(args: argparse.Namespace) -> None:
    cutoff = (now_local() - timedelta(days=args.days)).strftime("%Y-%m-%d")
    entries = _collect_entries_for_args(args, min_date=cutoff)
    raw_recent = [e for e in entries if str(e.get("ts") or "")[:10] >= cutoff]
    signal_recent = (
        raw_recent if args.include_reads else [e for e in raw_recent if _is_signal_entry(e)]
    )

    by_category: dict[str, int] = {}
    code_files: dict[str, str] = {}
    state_files: dict[str, str] = {}
    vault_syncs: list[str] = []
    staged_drafts: list[str] = []
    external_commitments: list[str] = []
    failure_traces: list[dict[str, str]] = []
    errors = 0

    for e in signal_recent:
        cat = str(e.get("category") or "OTHER")
        ws_label = str(e.get("_workspace") or CURRENT_WORKSPACE_NAME)
        by_category[cat] = by_category.get(cat, 0) + 1
        stat_str = str(e.get("status") or "")
        if stat_str != "OK" or cat == "PRE_TOOL_BLOCK":
            errors += 1
            failure_traces.append(
                {
                    "ts": str(e.get("ts") or "")[:19],
                    "workspace": ws_label,
                    "category": cat,
                    "tool": str(e.get("tool") or ""),
                    "target": _format_workspace_relpath(
                        str(e.get("target") or ""), ws_label, bool(args.all_workspaces)
                    ),
                    "status": stat_str,
                    "summary": str(e.get("summary") or "")[:180],
                }
            )
        if cat == "CODE_SYSTEM" and e.get("mutated") is not False:
            rel = _format_workspace_relpath(
                str(e.get("target") or ""), ws_label, bool(args.all_workspaces)
            )
            code_files[rel] = str(e.get("sha256_after") or "n/a")
        elif cat == "STATE_CACHE" and e.get("mutated") is not False:
            rel = _format_workspace_relpath(
                str(e.get("target") or ""), ws_label, bool(args.all_workspaces)
            )
            state_files[rel] = str(e.get("sha256_after") or "n/a")
        elif cat == "VAULT_SYNC":
            ts_short = str(e.get("ts") or "")[:16]
            vault_syncs.append(f"{ts_short} {e.get('target')}: {e.get('summary')}")
        elif cat == "STAGE_DRAFT":
            ts_short = str(e.get("ts") or "")[:16]
            staged_drafts.append(f"{ts_short} {e.get('summary') or e.get('target')}")
        elif cat == "EXTERNAL_ACTION":
            ts_short = str(e.get("ts") or "")[:16]
            external_commitments.append(f"{ts_short} {e.get('summary') or e.get('target')}")

    print(
        json.dumps(
            {
                "window_days": args.days,
                "since": cutoff,
                "all_workspaces": bool(args.all_workspaces),
                "raw_events_scanned": len(raw_recent),
                "signal_events": len(signal_recent),
                "error_events": errors,
                "counts_by_category": by_category,
                "code_system_files_touched": code_files,
                "state_cache_files_touched": state_files,
                "vault_syncs": vault_syncs[-20:],
                "staged_drafts": staged_drafts[-20:],
                "external_commitments": external_commitments[-20:],
                "failure_traces": failure_traces[-20:],
            },
            indent=2,
        )
    )


def cmd_changelog(args: argparse.Namespace) -> None:
    """Render a daily-grouped Keep-a-Changelog view (or JSON) from CODE_SYSTEM & ADR_DECISION events."""
    cutoff = (now_local() - timedelta(days=args.days)).strftime("%Y-%m-%d")
    entries = _collect_entries_for_args(args, min_date=cutoff)
    relevant = [
        e
        for e in entries
        if str(e.get("ts") or "")[:10] >= cutoff
        and e.get("category") in ("CODE_SYSTEM", "ADR_DECISION")
        and e.get("mutated") is not False
        and str(e.get("status") or "") == "OK"
    ]

    if not relevant:
        if args.format == "json":
            print(json.dumps({"since": cutoff, "days": {}}, indent=2))
        else:
            print(f"No CODE_SYSTEM or ADR_DECISION events recorded since {cutoff}.")
        return

    # Group CODE_SYSTEM events by (day, display_path) to collapse multi-edit spam into net daily spans.
    code_groups: dict[str, dict[str, dict[str, Any]]] = {}
    adr_groups: dict[str, list[dict[str, Any]]] = {}

    for e in relevant:
        ts_str = str(e.get("ts") or "")
        day = ts_str[:10]
        ts_time = ts_str[11:19]
        ws_label = str(e.get("_workspace") or CURRENT_WORKSPACE_NAME)
        cat = str(e.get("category") or "")

        if cat == "ADR_DECISION":
            adr_groups.setdefault(day, []).append(
                {
                    "time": ts_time,
                    "workspace": ws_label,
                    "target": str(e.get("target") or ""),
                    "summary": str(e.get("summary") or ""),
                    "sha256": f"{str(e.get('sha256_before') or '')[:8]}->{str(e.get('sha256_after') or '')[:8]}",
                    "git_head": str(e.get("git_head") or "none"),
                }
            )
            continue

        rel = _format_workspace_relpath(
            str(e.get("target") or ""), ws_label, bool(args.all_workspaces)
        )
        day_map = code_groups.setdefault(day, {})
        if rel not in day_map:
            day_map[rel] = {
                "path": rel,
                "first_sha_before": e.get("sha256_before"),
                "last_sha_after": e.get("sha256_after"),
                "last_bytes_after": e.get("bytes_after"),
                "edits": 1,
                "last_time": ts_time,
                "git_head": str(e.get("git_head") or "none"),
            }
        else:
            grp = day_map[rel]
            grp["last_sha_after"] = e.get("sha256_after")
            grp["last_bytes_after"] = e.get("bytes_after")
            grp["edits"] += 1
            grp["last_time"] = ts_time
            grp["git_head"] = str(e.get("git_head") or grp["git_head"])

    all_days = sorted(set(code_groups.keys()) | set(adr_groups.keys()), reverse=True)

    if args.format == "json":
        out_days: dict[str, Any] = {}
        for day in all_days:
            out_days[day] = {
                "code_changes": list(code_groups.get(day, {}).values()),
                "adr_decisions": adr_groups.get(day, []),
            }
        print(
            json.dumps(
                {
                    "since": cutoff,
                    "all_workspaces": bool(args.all_workspaces),
                    "days": out_days,
                },
                indent=2,
            )
        )
        return

    print(f"# DYNAMIC ARCHITECTURE & CODE CHANGELOG (Since {cutoff})")
    for day in all_days:
        print(f"\n## [{day}]")
        for rel, grp in sorted(code_groups.get(day, {}).items()):
            sha_b = grp["first_sha_before"]
            sha_a = grp["last_sha_after"]
            if sha_b and sha_a and sha_b == sha_a:
                continue
            action = "Added" if (not sha_b and sha_a) else "Changed"
            sha_str = f"{sha_b[:8]}->{sha_a[:8]}" if (sha_b and sha_a) else (str(sha_a or "n/a")[:8])
            edits_n = grp["edits"]
            edits_tag = f"{edits_n} edit" if edits_n == 1 else f"{edits_n} edits"
            size_b = grp["last_bytes_after"]
            size_tag = f", {size_b}B" if size_b is not None else ""
            print(
                f"- **[{action}]** `{rel}` ({edits_tag}{size_tag}, "
                f"`sha256:{sha_str}`, `git:{grp['git_head']}`, `{grp['last_time']}`)"
            )
        for adr in adr_groups.get(day, []):
            ws_tag = f"[{adr['workspace']}] " if args.all_workspaces else ""
            print(
                f"- **[ADR]** {ws_tag}`{adr['target']}` ({adr['summary']}, "
                f"`sha256:{adr['sha256']}`, `git:{adr['git_head']}`, `{adr['time']}`)"
            )


def _verify_single_log(base_log_path: str, ws_label: str) -> dict[str, Any]:
    entries, parse_errors = _read_entries_strict(
        base_log_path, workspace_label=ws_label, include_archives=True
    )
    corrupt_path = os.path.join(os.path.dirname(base_log_path), "audit_corrupt.jsonl")
    quarantined_count = 0
    if os.path.isfile(corrupt_path):
        try:
            with open(corrupt_path, "r", encoding="utf-8") as cf:
                quarantined_count = sum(1 for ln in cf if ln.strip())
        except OSError:
            pass

    if parse_errors:
        return {
            "workspace": ws_label,
            "status": "CHAIN_BROKEN",
            "reason": "Malformed JSON line(s) detected in audit log.",
            "parse_errors": parse_errors,
            "quarantined_tail_lines": quarantined_count,
        }

    if not entries:
        return {
            "workspace": ws_label,
            "status": "EMPTY_OK",
            "segments_verified": 0,
            "entries_verified": 0,
            "quarantined_tail_lines": quarantined_count,
        }

    expected_prev = GENESIS_HASH
    anchor_type = "GENESIS"
    broken: list[dict[str, Any]] = []

    for idx, e in enumerate(entries):
        line_no = e.get("_line")
        seg_name = e.get("_segment")
        recorded_hash = str(e.get("hash") or "")
        recorded_prev = str(e.get("prev_hash") or "")
        core = {
            k: v
            for k, v in e.items()
            if k not in ("hash", "_line", "_segment", "_workspace")
        }
        canonical = json.dumps(core, sort_keys=True, separators=(",", ":"))
        computed_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

        # If the earliest retained segment starts at a rotated checkpoint (after older .bak pruning),
        # anchor on its recorded_prev while strictly enforcing all subsequent links.
        if idx == 0 and e.get("rotated_checkpoint") is True and recorded_prev != GENESIS_HASH:
            expected_prev = recorded_prev
            anchor_type = "ROTATED_CHECKPOINT"

        if recorded_prev != expected_prev or recorded_hash != computed_hash:
            broken.append(
                {
                    "segment": seg_name,
                    "line": line_no,
                    "expected_prev": expected_prev[:16],
                    "recorded_prev": recorded_prev[:16],
                    "computed_hash": computed_hash[:16],
                    "recorded_hash": recorded_hash[:16],
                }
            )
        expected_prev = recorded_hash

    if broken:
        return {
            "workspace": ws_label,
            "status": "CHAIN_BROKEN",
            "entries_checked": len(entries),
            "violations": broken,
            "quarantined_tail_lines": quarantined_count,
        }

    return {
        "workspace": ws_label,
        "status": "VERIFIED_INTACT",
        "anchor": anchor_type,
        "segments_verified": len(_get_ordered_log_segments(base_log_path, include_archives=True)),
        "entries_verified": len(entries),
        "head_hash": expected_prev,
        "quarantined_tail_lines": quarantined_count,
    }


def cmd_verify(args: argparse.Namespace) -> None:
    if getattr(args, "all_workspaces", False):
        reports: dict[str, Any] = {}
        overall_ok = True
        total_entries = 0
        for ws in _active_workspace_names():
            ws_dir = _resolve_workspace_dir(ws)
            ws_log = os.path.join(ws_dir, "state/audit_log.jsonl")
            rep = _verify_single_log(ws_log, ws)
            reports[ws] = rep
            total_entries += int(rep.get("entries_verified") or 0)
            if rep["status"] not in ("VERIFIED_INTACT", "EMPTY_OK"):
                overall_ok = False
        out = {
            "status": "VERIFIED_INTACT" if overall_ok else "CHAIN_BROKEN",
            "all_workspaces": True,
            "total_entries_verified": total_entries,
            "workspaces": reports,
        }
        print(json.dumps(out, indent=2))
        if not overall_ok:
            sys.exit(1)
        return

    ws_label = CURRENT_WORKSPACE_NAME
    rep = _verify_single_log(args.log_file, ws_label)
    print(json.dumps(rep, indent=2))
    if rep["status"] not in ("VERIFIED_INTACT", "EMPTY_OK"):
        sys.exit(1)


def cmd_record_adr(args: argparse.Namespace) -> None:
    adr_path = Path(args.adr_file)
    if not adr_path.exists():
        sys.stderr.write(f"Error: ADR file not found: {adr_path}\n")
        sys.exit(1)

    today_str = now_local().strftime("%Y-%m-%d")
    ref_clean = str(args.ref or "").strip()
    ref_type_clean = str(args.ref_type or "PATTERN").upper()
    ref_suffix = f" *Ref ([{ref_type_clean}]):* `{ref_clean}`." if ref_clean else ""
    preview_entry = (
        f"- **[ADR-0000] [{today_str}] [{args.status.upper()}] {args.title.strip()}:** "
        f"*Decision:* {args.decision.strip()} *Consequence:* {args.consequences.strip()}{ref_suffix}"
    )
    sec_err = lint_security_payload(str(adr_path.name), preview_entry)
    if sec_err:
        sys.stderr.write(f"Rejected by {sec_err}\n")
        sys.exit(1)
    slop_err = lint_markdown_slop(str(adr_path), preview_entry)
    if slop_err:
        sys.stderr.write(f"Rejected by {slop_err}\n")
        sys.exit(1)

    with open(adr_path, "r+b") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        old_bytes = f.read()
        sha_before = hashlib.sha256(old_bytes).hexdigest()[:16]
        content = old_bytes.decode("utf-8")
        existing_nums = [int(m) for m in re.findall(r"\bADR-(\d{4})\b", content)]
        next_num = (max(existing_nums) + 1) if existing_nums else 1
        adr_id = f"ADR-{next_num:04d}"

        new_entry = (
            f"- **[{adr_id}] [{today_str}] [{args.status.upper()}] {args.title.strip()}:** "
            f"*Decision:* {args.decision.strip()} *Consequence:* {args.consequences.strip()}{ref_suffix}"
        )
        content = re.sub(
            r"\*\*Last Updated:\*\*\s*\d{4}-\d{2}-\d{2}",
            f"**Last Updated:** {today_str}",
            content,
            count=1,
        )
        content = content.rstrip() + "\n" + new_entry + "\n"
        new_bytes = content.encode("utf-8")
        sha_after = hashlib.sha256(new_bytes).hexdigest()[:16]
        f.seek(0)
        f.truncate()
        f.write(new_bytes)
        f.flush()
        os.fsync(f.fileno())

    summary_str = f"status={args.status.upper()}"
    if ref_clean:
        summary_str += f" ref_type={ref_type_clean} ref={ref_clean[:80]}"

    entry_core: dict[str, Any] = {
        "ts": now_local().isoformat(timespec="seconds"),
        "conv_id": "cli",
        "step_idx": None,
        "category": "ADR_DECISION",
        "tool": "record-adr",
        "target": f"{adr_id}: {args.title.strip()}",
        "summary": summary_str,
        "status": "OK",
        "sha256_before": sha_before,
        "sha256_after": sha_after,
        "bytes_after": len(new_bytes),
        "mutated": True,
        "git_head": git_head_short(WORKSPACE_ROOT),
    }
    append_locked_audit_entry(args.log_file, entry_core)

    print(
        json.dumps(
            {
                "status": "ADR_RECORDED",
                "adr_id": adr_id,
                "date": today_str,
                "ref_type": ref_type_clean if ref_clean else None,
                "ref": ref_clean or None,
                "adr_file": str(adr_path),
            },
            indent=2,
        )
    )


def remediate_epistemic_manifest(
    *,
    workspace_root: str = WORKSPACE_ROOT,
    log_path: str = DEFAULT_AUDIT_LOG,
    ack_drift: str | None = None,
    clear_taint: str | None = None,
    drop_reply: str | None = None,
    ack_incidents: bool = False,
) -> list[str]:
    """Closed-loop fcntl.flock remediation for epistemic_manifest.json with audit_log.jsonl provenance."""
    if not (ack_drift or clear_taint or drop_reply or ack_incidents):
        return []
    actions: list[str] = []
    manifest_p = os.path.join(workspace_root, "state/epistemic_manifest.json")
    ts_iso = now_local().isoformat(timespec="seconds")
    os.makedirs(os.path.dirname(manifest_p), exist_ok=True)
    with open(manifest_p, "a+", encoding="utf-8") as mf:
        fcntl.flock(mf.fileno(), fcntl.LOCK_EX)
        mf.seek(0)
        raw = mf.read().strip()
        try:
            data = json.loads(raw) if raw else {}
            if not isinstance(data, dict):
                data = {}
        except json.JSONDecodeError:
            data = {}

        if ack_drift:
            v_sources = data.setdefault("vault_sources", {})
            target_d = ack_drift.strip()
            for uid, rec in v_sources.items():
                if not isinstance(rec, dict):
                    continue
                if target_d.upper() == "ALL" or uid.startswith(target_d) or rec.get("label") == target_d:
                    rec["remote_drift_detected"] = False
                    if rec.get("read_sha256"):
                        rec["sync_sha256"] = rec["read_sha256"]
                    rec["last_sync_ts"] = ts_iso
                    actions.append(f"ack_drift={rec.get('label', uid[:8])}")

        if clear_taint:
            c_map = data.setdefault("conv_reads", {})
            target_t = clear_taint.strip()
            for cid, cmeta in c_map.items():
                if not isinstance(cmeta, dict):
                    continue
                if target_t.upper() == "ALL" or cid.startswith(target_t):
                    if cmeta.get("taint_sources"):
                        cmeta["taint_sources"] = []
                        cmeta.pop("taint_user_input_sha256", None)
                        actions.append(f"clear_taint={cid}")

        if drop_reply:
            r_map = data.setdefault("staged_replies", {})
            target_r = drop_reply.strip()
            for rid, rmeta in r_map.items():
                if not isinstance(rmeta, dict):
                    continue
                if target_r.upper() == "ALL" or rid.upper() == target_r.upper():
                    if rmeta.get("status") == "STAGED":
                        rmeta["status"] = "DROPPED"
                        rmeta["dropped_ts"] = ts_iso
                        actions.append(f"drop_reply={rid}")

        if ack_incidents:
            s_list = data.get("security_incidents") or []
            acked_n = 0
            for inc in s_list:
                if isinstance(inc, dict) and not inc.get("acknowledged"):
                    inc["acknowledged"] = True
                    inc["ack_ts"] = ts_iso
                    acked_n += 1
            actions.append(f"ack_incidents={acked_n}")

        if actions:
            data["updated_at"] = ts_iso
            mf.seek(0)
            mf.truncate()
            mf.write(json.dumps(data, indent=2) + "\n")
            mf.flush()

    if actions:
        sha_after, bytes_after = file_sha256_and_size(manifest_p)
        append_locked_audit_entry(
            log_path,
            {
                "ts": ts_iso,
                "conv_id": "cli",
                "step_idx": None,
                "category": "STATE_CACHE",
                "tool": "audit_log.py:epistemic",
                "target": manifest_p,
                "summary": f"epistemic_remediation ({', '.join(actions)})",
                "status": "OK",
                "sha256_before": None,
                "sha256_after": sha_after[:16] if sha_after else None,
                "bytes_after": bytes_after,
                "mutated": True,
                "git_head": git_head_short(workspace_root),
            },
        )
    return actions


def compute_epistemic_status(
    workspace_root: str = WORKSPACE_ROOT,
    ecosystem_root: str = ECOSYSTEM_ROOT,
    window_days: int = 14,
) -> dict[str, Any]:
    """O(1) structured bitemporal & W3C PROV epistemic engine backed strictly by epistemic_manifest.json."""
    now_dt = now_local(workspace_root, use_showcase_anchor=True)
    today = now_dt.date()
    cutoff_date = (now_dt - timedelta(days=window_days)).strftime("%Y-%m-%d")
    dangling_cutoff_dt = now_dt - timedelta(seconds=DANGLING_REPLY_TTL_SECONDS)

    manifest_path = os.path.join(workspace_root, "state/epistemic_manifest.json")
    m_obj = _load_epistemic_manifest(manifest_path)
    manifest_sources = m_obj.get("vault_sources") if isinstance(m_obj.get("vault_sources"), dict) else {}

    raw_ws = os.path.basename(workspace_root.rstrip("/"))
    current_ws = raw_ws if raw_ws in ALL_WORKSPACE_NAMES else "claire-architect"

    ws_manifests: dict[str, dict[str, Any]] = {}
    for ws in ALL_WORKSPACE_NAMES:
        ws_dir = workspace_root if ws == current_ws else os.path.join(ecosystem_root, ws)
        ws_manifests[ws] = (
            m_obj
            if ws == current_ws
            else _load_epistemic_manifest(os.path.join(ws_dir, "state/epistemic_manifest.json"))
        )

    vault_tracker: dict[str, dict[str, Any]] = {}
    for uid, spec in VAULT_SOURCE_SPECS.items():
        owner_ws = str(spec.get("owner") or "claire-architect")
        owner_sources = (ws_manifests.get(owner_ws) or {}).get("vault_sources")
        m_rec = (
            manifest_sources.get(uid)
            or (owner_sources.get(uid) if isinstance(owner_sources, dict) else None)
            or {}
        )
        vault_tracker[uid] = {
            "uuid": uid,
            "label": spec["label"],
            "volatility": spec["volatility"],
            "ttl_days": spec["ttl_days"],
            "owner": owner_ws,
            "last_read_ts": m_rec.get("last_read_ts"),
            "last_sync_ts": m_rec.get("last_sync_ts"),
            "read_sha256": m_rec.get("read_sha256") or m_rec.get("last_content_sha256"),
            "sync_sha256": m_rec.get("sync_sha256"),
            "remote_drift_detected": bool(m_rec.get("remote_drift_detected", False)),
            "epistemic_state": "ASSUMED_UNVERIFIED",
        }

    staged_replies: dict[str, dict[str, str]] = {}
    sent_replies: set[str] = set()
    for r_id, r_rec in (m_obj.get("staged_replies") or {}).items():
        if isinstance(r_rec, dict):
            if r_rec.get("status") in ("SENT", "DROPPED"):
                sent_replies.add(r_id)
            elif r_rec.get("status") == "STAGED":
                staged_replies[r_id] = {
                    "rpl_id": r_id,
                    "ts": str(r_rec.get("ts") or ""),
                    "workspace": "claire-architect",
                    "summary": "manifest",
                }

    latest_vault_sync_ts: str | None = m_obj.get("latest_vault_sync_ts")
    latest_local_state_mut_ts: str | None = m_obj.get("latest_local_state_mut_ts")

    tier_counts: dict[str, int] = {
        "TIER_1_UPSTREAM_SPEC": 0,
        "TIER_2_EMPIRICAL_PRACTITIONER": 0,
        "TIER_3_UNVERIFIED_WEB": 0,
    }
    recent_external_lookups: list[dict[str, Any]] = []
    for ws_m in ws_manifests.values():
        for m_ext in ws_m.get("external_lookups") or []:
            if isinstance(m_ext, dict):
                t_val = str(m_ext.get("tier") or "TIER_3_UNVERIFIED_WEB")
                if t_val in tier_counts:
                    tier_counts[t_val] += 1
                recent_external_lookups.append(m_ext)

    unverified_active_vault_sources: list[str] = []
    remote_drift_sources: list[str] = []
    for uid, vt in vault_tracker.items():
        ttl_d = vt["ttl_days"]
        last_r = str(vt.get("last_read_ts") or "")
        last_s = str(vt.get("last_sync_ts") or "")
        last_touch = max(last_r, last_s)
        if vt["remote_drift_detected"]:
            vt["epistemic_state"] = "OUT_OF_BAND_DRIFT"
            remote_drift_sources.append(f"{vt['label']} ({uid[:8]})")
        elif ttl_d is None:
            vt["epistemic_state"] = (
                "SYNCHRONIZED" if last_s else ("OBSERVED" if last_r else "DORMANT_EXEMPT")
            )
        elif not last_touch:
            vt["epistemic_state"] = "ASSUMED_UNVERIFIED"
            unverified_active_vault_sources.append(
                f"{vt['label']} ({uid[:8]}, TTL={ttl_d}d)"
            )
        else:
            try:
                touch_date = datetime.strptime(last_touch[:10], "%Y-%m-%d").date()
                age_d = (today - touch_date).days
            except ValueError:
                age_d = 999
            if age_d > ttl_d:
                vt["epistemic_state"] = "STALE_TTL_EXPIRED"
                unverified_active_vault_sources.append(
                    f"{vt['label']} ({uid[:8]}, age={age_d}d > TTL={ttl_d}d)"
                )
            elif last_s >= last_r and last_s:
                vt["epistemic_state"] = "SYNCHRONIZED"
            else:
                vt["epistemic_state"] = "OBSERVED"

    local_cache_dirty = bool(
        latest_local_state_mut_ts
        and (not latest_vault_sync_ts or latest_local_state_mut_ts > latest_vault_sync_ts)
    )

    local_state_matrix: dict[str, dict[str, Any]] = {}
    for label, fpath, max_lines, max_bytes, max_stale_days in get_workspace_state_files_to_audit(workspace_root):
        sha_f, bytes_f = file_sha256_and_size(fpath)
        lines_f = 0
        header_date: str | None = None
        age_days: int | None = None
        if os.path.isfile(fpath):
            try:
                with open(fpath, "r", encoding="utf-8") as sf:
                    txt = sf.read()
                lines_f = len(txt.splitlines())
                m_dt = re.search(
                    r"(?:\*\*Last Updated:\*\*|\*\*Last Synced:\*\*|last_updated:)\s*(\d{4}-\d{2}-\d{2})",
                    txt,
                    re.IGNORECASE,
                )
                if m_dt:
                    header_date = m_dt.group(1)
                    age_days = (today - datetime.strptime(header_date, "%Y-%m-%d").date()).days
            except Exception:
                pass
        stale_flag = bool(max_stale_days is not None and age_days is not None and age_days > max_stale_days)
        over_cap = bool((bytes_f or 0) > max_bytes or lines_f > max_lines)
        local_state_matrix[label] = {
            "exists": os.path.isfile(fpath),
            "sha256": sha_f[:16] if sha_f else None,
            "bytes": bytes_f,
            "lines": lines_f,
            "header_last_updated": header_date,
            "age_days": age_days,
            "ttl_days": max_stale_days,
            "stale": stale_flag,
            "over_cap": over_cap,
        }

    reg_path = os.path.join(workspace_root, "state/ecosystem_registry.md")
    if not os.path.isfile(reg_path):
        reg_path = os.path.join(ecosystem_root, "claire-architect", "state/ecosystem_registry.md")
    reg_uuids: set[str] = set()
    if os.path.isfile(reg_path):
        try:
            with open(reg_path, "r", encoding="utf-8") as rf:
                reg_uuids = {u.lower() for u in UUID_EXTRACT_RE.findall(rf.read())}
        except OSError:
            pass
    hook_uuids = set(ECOSYSTEM_REGISTRY_UUIDS)
    missing_in_hooks = sorted(reg_uuids - hook_uuids)
    missing_in_registry = sorted(hook_uuids - reg_uuids)
    uuid_congruent = not missing_in_hooks and not missing_in_registry

    workspace_congruence: dict[str, dict[str, Any]] = {}
    suppressed_anomalies: list[str] = (
        validate_domain_vault_manifests(ecosystem_root)
        + validate_persona_and_panel_congruence(ecosystem_root)
    )

    for ws in _active_workspace_names(workspace_root, ecosystem_root):
        ws_dir = workspace_root if ws == current_ws else os.path.join(ecosystem_root, ws)
        ep_p = os.path.join(ws_dir, "state/epistemic_manifest.json")
        if os.path.isfile(ep_p) and os.path.getsize(ep_p) == 0:
            suppressed_anomalies.append(f"{ws}/state/epistemic_manifest.json is 0B (uninitialized)")
        sp_path = os.path.join(ws_dir, "state/active_sprint.md")
        sp_date: str | None = None
        if os.path.isfile(sp_path):
            try:
                with open(sp_path, "r", encoding="utf-8") as spf:
                    m_sp = re.search(
                        r"(?:\*\*Last Updated:\*\*|last_updated:)\s*(\d{4}-\d{2}-\d{2})",
                        spf.read(),
                        re.IGNORECASE,
                    )
                    if m_sp:
                        sp_date = m_sp.group(1)
            except OSError:
                pass
        log_p = os.path.join(ws_dir, "state/audit_log.jsonl")
        log_bytes = os.path.getsize(log_p) if os.path.isfile(log_p) else 0
        for err_rel in ("state/hook_errors.log", "state/audit_corrupt.jsonl"):
            err_p = os.path.join(ws_dir, err_rel)
            if os.path.isfile(err_p) and os.path.getsize(err_p) > 0:
                suppressed_anomalies.append(f"{ws}/{err_rel} ({os.path.getsize(err_p)}B)")

        workspace_congruence[ws] = {
            "active_sprint_date": sp_date,
            "audit_log_bytes": log_bytes,
            "manifest_updated_at": ws_manifests.get(ws, {}).get("updated_at"),
        }

    dangling_replies: list[str] = []
    for rpl_id, item in sorted(staged_replies.items()):
        if rpl_id in sent_replies:
            continue
        try:
            rpl_dt = datetime.fromisoformat(item["ts"])
            if rpl_dt.tzinfo is None:
                rpl_dt = rpl_dt.replace(tzinfo=LOCAL_TZ)
            if rpl_dt.astimezone(LOCAL_TZ) >= dangling_cutoff_dt:
                dangling_replies.append(f"{rpl_id} (staged {item['ts'][:16]} in {item['workspace']})")
        except ValueError:
            pass

    adr_with_external_refs = 0
    adr_file_path = os.path.join(workspace_root, "state/archive_decisions.md")
    if os.path.isfile(adr_file_path):
        try:
            with open(adr_file_path, "r", encoding="utf-8") as af:
                for line in af:
                    if line.lstrip().startswith(("- **[ADR-", "- **`ADR-")) and any(
                        tag in line
                        for tag in ("[SPEC]", "[PATTERN]", "[BENCHMARK]", "[SPEC:", "[PATTERN:", "[BENCHMARK:", "Ref", "ref=")
                    ):
                        adr_with_external_refs += 1
        except OSError:
            pass

    sec_incidents: list[dict[str, Any]] = []
    tainted_convs: list[str] = []
    for ws_name, ws_m in ws_manifests.items():
        for inc in ws_m.get("security_incidents") or []:
            if isinstance(inc, dict) and not inc.get("acknowledged"):
                sec_incidents.append(inc)
        for cid, cmeta in (ws_m.get("conv_reads") or {}).items():
            if isinstance(cmeta, dict) and cmeta.get("taint_sources"):
                tainted_convs.append(
                    f"{ws_name}:{cid} ({','.join(cmeta.get('taint_sources') or [])})"
                )

    return {
        "evaluated_at": now_dt.isoformat(timespec="seconds"),
        "window_days": window_days,
        "cutoff_date": cutoff_date,
        "how_we_know": {
            "local_state_cache": local_state_matrix,
            "vault_sources": vault_tracker,
            "latest_local_state_mutation_ts": latest_local_state_mut_ts,
            "latest_vault_sync_ts": latest_vault_sync_ts,
            "local_cache_dirty_vs_vault": local_cache_dirty,
            "exogenous_grounding": {
                "tier_counts": tier_counts,
                "recent_lookups": recent_external_lookups[-10:],
                "adr_external_citations": adr_with_external_refs,
            },
            "security_and_taint": {
                "tainted_conversations": tainted_convs,
                "security_incidents": sec_incidents[-10:],
            },
        },
        "congruence_and_conflicts": {
            "uuid_topology_congruent": uuid_congruent,
            "missing_in_hooks": missing_in_hooks,
            "missing_in_registry": missing_in_registry,
            "remote_drift_sources": remote_drift_sources,
            "suppressed_system_anomalies": suppressed_anomalies,
            "workspaces": workspace_congruence,
        },
        "known_unknowns": {
            "unverified_active_vault_sources": unverified_active_vault_sources,
            "dangling_staged_replies_24h": dangling_replies,
        },
    }


def cmd_epistemic(args: argparse.Namespace) -> None:
    remediated = remediate_epistemic_manifest(
        workspace_root=WORKSPACE_ROOT,
        log_path=args.log_file,
        ack_drift=getattr(args, "ack_drift", None),
        clear_taint=getattr(args, "clear_taint", None),
        drop_reply=getattr(args, "drop_reply", None),
        ack_incidents=bool(getattr(args, "ack_incidents", False)),
    )
    report = compute_epistemic_status(
        workspace_root=WORKSPACE_ROOT,
        ecosystem_root=ECOSYSTEM_ROOT,
        window_days=args.days,
    )
    if remediated:
        report["remediated_actions"] = remediated

    cong = report["congruence_and_conflicts"]
    sec_taint = report["how_we_know"].get("security_and_taint") or {}
    has_blocking_issue = bool(
        not cong.get("uuid_topology_congruent", True)
        or cong.get("remote_drift_sources")
        or cong.get("suppressed_system_anomalies")
        or sec_taint.get("security_incidents")
    )

    if args.format == "json" or getattr(args, "json_flag", False):
        print(json.dumps(report, indent=2))
        if getattr(args, "check", False) and has_blocking_issue:
            sys.exit(1)
        return

    print(
        f"# EPISTEMIC ENGINE REPORT (Evaluated: {report['evaluated_at']} | Window: {report['window_days']}d)"
    )
    if remediated:
        print(f"- **Remediation Applied:** `{', '.join(remediated)}`")

    hwk = report["how_we_know"]
    print("\n## 1. Endogenous Provenance & Exogenous Authority")
    print(
        f"- **L1 Cache vs. Vault Write-Back Status:** "
        f"`{'DIRTY' if hwk.get('local_cache_dirty_vs_vault') else 'CLEAN'}` "
        f"(latest_local_mut=`{hwk.get('latest_local_state_mutation_ts') or 'none'}`, "
        f"latest_vault_sync=`{hwk.get('latest_vault_sync_ts') or 'none'}`)"
    )
    for fname, meta in hwk["local_state_cache"].items():
        status_tag = "STALE/CAP_ALERT" if (meta["stale"] or meta["over_cap"]) else "OK"
        ttl_str = f"{meta['ttl_days']}d" if meta.get("ttl_days") is not None else "exempt"
        print(
            f"- **Local Cache `{fname}`:** `{status_tag}` "
            f"(`sha256:{meta['sha256']}`, `{meta['bytes']}B`, `{meta['lines']}L`, "
            f"header=`{meta['header_last_updated']}`, age=`{meta['age_days']}d`, ttl=`{ttl_str}`)"
        )
    for uid, vmeta in hwk["vault_sources"].items():
        r_ts = vmeta.get("last_read_ts") or "never"
        s_ts = vmeta.get("last_sync_ts") or "never"
        r_sha = vmeta.get("read_sha256") or "unhashed"
        s_sha = vmeta.get("sync_sha256") or "unhashed"
        ttl_str = f"{vmeta.get('ttl_days')}d" if vmeta.get("ttl_days") is not None else "exempt"
        print(
            f"- **Vault `{vmeta['label']}` (`{uid[:8]}`):** `{vmeta.get('epistemic_state')}` "
            f"[{vmeta.get('volatility')}, ttl=`{ttl_str}`] | "
            f"read=`{r_ts}` (`{r_sha}`), sync=`{s_ts}` (`{s_sha}`)"
        )
    exo = hwk.get("exogenous_grounding") or {}
    tc = exo.get("tier_counts") or {}
    print(
        f"- **Exogenous Lookup Authority Distribution:** "
        f"Tier-1=`{tc.get('TIER_1_UPSTREAM_SPEC', 0)}`, "
        f"Tier-2=`{tc.get('TIER_2_EMPIRICAL_PRACTITIONER', 0)}`, "
        f"Tier-3=`{tc.get('TIER_3_UNVERIFIED_WEB', 0)}` | "
        f"ADRs with External Citations=`{exo.get('adr_external_citations', 0)}`"
    )
    sec_taint = hwk.get("security_and_taint") or {}
    t_convs = sec_taint.get("tainted_conversations") or []
    s_incs = sec_taint.get("security_incidents") or []
    print(
        f"- **CaMeL Taint & Prompt-Injection Defense:** "
        f"Tainted Sessions=`{len(t_convs)}` ({', '.join(t_convs) if t_convs else 'none'}), "
        f"Unacknowledged Incidents=`{len(s_incs)}`"
    )

    print("\n## 2. Congruence & Pending Commitments")
    print(f"- **UUID Topology Congruent (`ecosystem_registry.md` == `core_hooks.py`):** `{cong['uuid_topology_congruent']}`")
    if cong["remote_drift_sources"]:
        print(f"- **Out-of-Band Remote Vault Drift:** `{', '.join(cong['remote_drift_sources'])}`")
    if cong.get("suppressed_system_anomalies"):
        print(f"- **System Anomalies:** `{', '.join(cong['suppressed_system_anomalies'])}`")
    ku = report["known_unknowns"]
    print(
        f"- **Unverified Active Vault Sources (outside TTL):** "
        f"`{len(ku['unverified_active_vault_sources'])}` "
        f"({', '.join(ku['unverified_active_vault_sources']) if ku['unverified_active_vault_sources'] else 'none'})"
    )
    print(
        f"- **Dangling Staged SMS Replies (`<24h`):** "
        f"`{len(ku['dangling_staged_replies_24h'])}` "
        f"({', '.join(ku['dangling_staged_replies_24h']) if ku['dangling_staged_replies_24h'] else 'none'})"
    )
    if getattr(args, "check", False) and has_blocking_issue:
        sys.exit(1)


def cmd_tokens(args: argparse.Namespace) -> None:
    """Audit Turn-0 character/token footprint and anti-slop compliance across all 5 workspaces."""
    violations: list[str] = validate_persona_and_panel_congruence(ECOSYSTEM_ROOT)
    rows: list[dict[str, Any]] = []
    tot_p_t = tot_v_t = tot_s_t = tot_all_t = 0

    for label, folder, prompt_rel, fallback_prompt_rel, voice_rel, state_rels, fallback_state_chars in WORKSPACE_TURN0_SPECS:
        ws_dir = WORKSPACE_ROOT if folder == CURRENT_WORKSPACE_NAME else os.path.join(ECOSYSTEM_ROOT, folder)
        using_fallback = False

        def _read_len(base_dir: str, rel_p: str) -> tuple[int, str]:
            fp = os.path.join(base_dir, rel_p)
            if not os.path.isfile(fp):
                return 0, ""
            with open(fp, "r", encoding="utf-8") as f:
                txt = f.read()
            return len(txt), txt

        p_chars, p_txt = _read_len(ws_dir, prompt_rel)
        checked_prompt_rel = prompt_rel
        if not p_chars and fallback_prompt_rel:
            p_chars, p_txt = _read_len(WORKSPACE_ROOT, fallback_prompt_rel)
            if p_chars:
                using_fallback = True
                checked_prompt_rel = fallback_prompt_rel

        v_chars, _ = _read_len(ws_dir, voice_rel)
        if not v_chars:
            v_chars, _ = _read_len(WORKSPACE_ROOT, voice_rel)

        s_chars = 0
        for sr in state_rels:
            sc, stxt = _read_len(ws_dir, sr)
            s_chars += sc
            if stxt:
                err = lint_markdown_slop(sr, stxt)
                if err:
                    violations.append(f"{folder}/{sr}: {err}")
                if HIDDEN_UNICODE_RE.search(stxt):
                    violations.append(f"{folder}/{sr}: hidden Unicode detected")
        if not s_chars and fallback_state_chars:
            s_chars = fallback_state_chars
            using_fallback = True
        if p_txt:
            err = lint_markdown_slop(checked_prompt_rel, p_txt)
            if err:
                violations.append(f"{folder}/{checked_prompt_rel}: {err}")
            if HIDDEN_UNICODE_RE.search(p_txt):
                violations.append(f"{folder}/{checked_prompt_rel}: hidden Unicode detected")

        p_t = round(p_chars / 3.8)
        v_t = round(v_chars / 3.8)
        s_t = round(s_chars / 3.8)
        turn0_t = p_t + v_t + s_t
        tot_p_t += p_t
        tot_v_t += v_t
        tot_s_t += s_t
        tot_all_t += turn0_t
        rows.append(
            {
                "workspace": label,
                "folder": folder,
                "source_mode": "showcase_spec" if using_fallback else "live_workspace",
                "prompt_chars": p_chars,
                "prompt_tokens": p_t,
                "voice_chars": v_chars,
                "voice_tokens": v_t,
                "state_chars": s_chars,
                "state_tokens": s_t,
                "turn0_tokens": turn0_t,
            }
        )

    if getattr(args, "format", "markdown") == "json":
        print(
            json.dumps(
                {
                    "workspaces": rows,
                    "total_turn0_tokens": tot_all_t,
                    "violations": violations,
                },
                indent=2,
            )
        )
    else:
        print("## Verified Turn-0 Telemetry Table")
        for r in rows:
            print(
                f"| **{r['workspace']}** (`{r['folder']}`) | {r['prompt_chars']:,} c (~{r['prompt_tokens']:,} t) "
                f"| {r['voice_chars']:,} c (~{r['voice_tokens']:,} t) | {r['state_chars']:,} c (~{r['state_tokens']:,} t) "
                f"| **~{r['turn0_tokens']:,} tokens** |"
            )
        print(
            f"| **ECOSYSTEM TOTAL** | **~{tot_p_t:,} t** | **~{tot_v_t:,} t** "
            f"| **~{tot_s_t:,} t** | **~{tot_all_t:,} tokens** |"
        )
    if violations:
        for v in violations:
            sys.stderr.write(f"Violation: {v}\n")
        sys.exit(1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Query and verify the SHA-256 hash-chained agent audit log and manage ADR archives."
    )
    parser.add_argument(
        "--log-file",
        default=DEFAULT_AUDIT_LOG,
        help="Path to state/audit_log.jsonl.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_q = sub.add_parser("query", help="Filter and display audit log events (signal-first by default).")
    p_q.add_argument("--since", help="Filter events on or after YYYY-MM-DD.")
    p_q.add_argument("--until", help="Filter events on or before YYYY-MM-DD.")
    p_q.add_argument(
        "--category",
        help="Filter by category (CODE_SYSTEM, STATE_CACHE, VAULT_SYNC, STAGE_DRAFT, EXTERNAL_ACTION, SUBAGENT, EXEC_CMD, ADR_DECISION, READ_QUERY, ARTIFACT_STAGE, ALL).",
    )
    p_q.add_argument("--target", help="Case-insensitive substring match on target or summary.")
    p_q.add_argument("--status", help="Filter by status prefix (OK or ERROR).")
    p_q.add_argument(
        "--include-reads",
        action="store_true",
        help="Include READ_QUERY, ARTIFACT_STAGE, and no-op (mutated=False) events.",
    )
    p_q.add_argument(
        "--include-archives",
        action="store_true",
        help="Force scanning all rotated .bak segments even if active log covers the date window.",
    )
    p_q.add_argument(
        "--all-workspaces",
        "-a",
        action="store_true",
        help="Aggregate audit logs across all 5 workspaces (claire-architect, elmer, chad, andy, grant).",
    )
    p_q.add_argument("--limit", type=int, default=50, help="Max events to return (default: 50).")
    p_q.add_argument("--format", choices=("table", "json"), default="table", help="Output format.")
    p_q.set_defaults(func=cmd_query)

    p_sum = sub.add_parser(
        "summary",
        help="Print a rolling signal digest of tool counts, mutated files, Vault syncs, and external commitments.",
    )
    p_sum.add_argument("--days", type=int, default=14, help="Rolling window in days (default: 14).")
    p_sum.add_argument(
        "--include-reads",
        action="store_true",
        help="Include READ_QUERY, ARTIFACT_STAGE, and no-op events in category counts.",
    )
    p_sum.add_argument(
        "--include-archives",
        action="store_true",
        help="Force scanning all rotated .bak segments.",
    )
    p_sum.add_argument(
        "--all-workspaces",
        "-a",
        action="store_true",
        help="Aggregate across all 5 workspaces.",
    )
    p_sum.set_defaults(func=cmd_summary)

    p_cl = sub.add_parser(
        "changelog",
        help="Render a daily-grouped Keep-a-Changelog view directly from CODE_SYSTEM & ADR_DECISION events.",
    )
    p_cl.add_argument("--days", type=int, default=14, help="Rolling window in days (default: 14).")
    p_cl.add_argument(
        "--include-archives",
        action="store_true",
        help="Force scanning all rotated .bak segments.",
    )
    p_cl.add_argument(
        "--all-workspaces",
        "-a",
        action="store_true",
        help="Aggregate across all 5 workspaces.",
    )
    p_cl.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="Output format (markdown or json).",
    )
    p_cl.set_defaults(func=cmd_changelog)

    p_v = sub.add_parser(
        "verify",
        help="Verify SHA-256 hash chain integrity across rotated .bak segments and audit_log.jsonl.",
    )
    p_v.add_argument(
        "--all-workspaces",
        "-a",
        action="store_true",
        help="Verify hash chains across all 5 workspaces in one JSON report.",
    )
    p_v.set_defaults(func=cmd_verify)

    p_a = sub.add_parser("record-adr", help="Append a structured ADR-XXXX record to archive_decisions.md.")
    p_a.add_argument("--title", required=True, help="Short title of the architectural decision.")
    p_a.add_argument("--decision", required=True, help="1-sentence description of what was decided.")
    p_a.add_argument("--consequences", required=True, help="1-sentence operational impact or trade-off.")
    p_a.add_argument(
        "--status",
        default="ACCEPTED",
        choices=("ACCEPTED", "SUPERSEDED", "KILLED", "COMPLETED"),
        help="Decision status.",
    )
    p_a.add_argument(
        "--ref",
        default="",
        help="Optional external specification or empirical practitioner reference (e.g., 'getzep/graphiti', 'w3.org/TR/prov-o').",
    )
    p_a.add_argument(
        "--ref-type",
        default="PATTERN",
        choices=("SPEC", "PATTERN", "BENCHMARK"),
        help="Whether --ref is a binding upstream SPEC, an optional practitioner PATTERN, or a local BENCHMARK.",
    )
    p_a.add_argument("--adr-file", default=DEFAULT_ADR_FILE, help="Path to archive_decisions.md.")
    p_a.set_defaults(func=cmd_record_adr)

    p_ep = sub.add_parser(
        "epistemic",
        help="Evaluate and remediate endogenous provenance, exogenous authority, CaMeL taint, and counterfactual blind spots.",
    )
    p_ep.add_argument("--days", type=int, default=14, help="Rolling verification window in days (default: 14).")
    p_ep.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="Output format (markdown or json).",
    )
    p_ep.add_argument("--json", dest="json_flag", action="store_true", help="Alias for --format json.")
    p_ep.add_argument(
        "--check",
        action="store_true",
        help="Exit with code 1 if UUID topology mismatch, remote Vault drift, or unacknowledged security incident is active.",
    )
    p_ep.add_argument(
        "--ack-drift",
        help="Acknowledge verified out-of-band Vault drift for a UUID prefix, source label, or ALL.",
    )
    p_ep.add_argument(
        "--clear-taint",
        help="Clear CaMeL taint_sources for a conversation ID prefix or ALL after human review.",
    )
    p_ep.add_argument(
        "--drop-reply",
        help="Mark a canceled staged SMS reply (RPL-xxxxxx or ALL) as DROPPED.",
    )
    p_ep.add_argument(
        "--ack-incidents",
        action="store_true",
        help="Acknowledge logged security incidents so SessionStart stops nagging for 24h.",
    )
    p_ep.set_defaults(func=cmd_epistemic)

    p_tok = sub.add_parser(
        "tokens",
        help="Audit Turn-0 character/token footprint and anti-slop compliance across all 5 workspaces.",
    )
    p_tok.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="Output format (markdown or json).",
    )
    p_tok.set_defaults(func=cmd_tokens)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
