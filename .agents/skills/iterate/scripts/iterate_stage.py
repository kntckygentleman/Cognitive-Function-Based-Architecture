#!/usr/bin/env python3
"""Isolated Staging & Snapshot Manager for the /iterate Skill.

Enforces the core /iterate invariant:
- Never modifies live workspace files or commits/pushes to GitHub during iteration rounds.
- Mirrors target files/directories into an isolated staging directory (<stage_dir>/current/).
- After each round's edits are applied to <stage_dir>/current/, validates all staged Markdown
  files against the human-voice.md anti-slop rules, records a numbered snapshot in
  <stage_dir>/rounds/round_<N>/, and prints a portable manifest of modified files and token
  deltas for the Master Plan artifact.
- Provides an explicit `apply` subcommand to copy vetted staged files to a named target
  environment only when the operator explicitly instructs the workspace to submit the plan.
"""

from __future__ import annotations

import argparse
import ast
from datetime import datetime, timedelta, timezone
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

try:
    from core_hooks import (  # noqa: E402
        ALL_WORKSPACE_NAMES,
        ECOSYSTEM_ROOT,
        HIDDEN_UNICODE_RE,
        lint_markdown_slop,
    )
except ImportError:
    from claire_hooks import (  # noqa: E402
        ALL_WORKSPACE_NAMES,
        ECOSYSTEM_ROOT,
        HIDDEN_UNICODE_RE,
        lint_markdown_slop,
    )

IGNORE_NAMES = {
    ".git",
    "__pycache__",
    ".DS_Store",
    ".iterate_stage",
    "iterate_stage",
    ".venv",
    "venv",
    "node_modules",
}

SHARED_ECOSYSTEM_REL_PATHS = (
    ".agents/rules/human-voice.md",
    ".agents/scripts/core_hooks.py",
    ".agents/scripts/audit_log.py",
    ".agents/skills/iterate/SKILL.md",
    ".agents/skills/iterate/references/critic_panels.md",
    ".agents/skills/iterate/scripts/iterate_stage.py",
)


def _sync_shared_ecosystem_files_in_stage(current_dir: Path) -> None:
    """Propagate canonical claire-architect shared files across staged peer workspaces."""
    claire_dir = current_dir / "claire-architect"
    if not claire_dir.is_dir():
        return
    for shared_rel in SHARED_ECOSYSTEM_REL_PATHS:
        canon_src = claire_dir / shared_rel
        if not canon_src.is_file():
            continue
        for ws in ALL_WORKSPACE_NAMES:
            if ws == "claire-architect":
                continue
            peer_ws_dir = current_dir / ws
            peer_dst = peer_ws_dir / shared_rel
            if peer_dst.parent.is_dir() or peer_dst.is_file():
                shutil.copy2(canon_src, peer_dst)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def collect_tree_stats(root: Path) -> dict[str, dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {}
    if not root.exists():
        return stats
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in sorted(dirnames) if d not in IGNORE_NAMES]
        for fn in sorted(filenames):
            if fn in IGNORE_NAMES:
                continue
            full = Path(dirpath) / fn
            if full.is_symlink():
                continue
            rel = str(full.relative_to(root))
            size = full.stat().st_size
            digest = sha256_file(full)
            lines = 0
            chars = 0
            try:
                text = full.read_text(encoding="utf-8")
                chars = len(text)
                lines = len(text.splitlines())
            except Exception:
                pass
            stats[rel] = {
                "sha256": digest,
                "bytes": size,
                "lines": lines,
                "chars": chars,
                "est_tokens": round(chars / 3.8) if chars else 0,
            }
    return stats


def copy_filtered_tree(src: Path, dst: Path) -> None:
    if src.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = [d for d in sorted(dirnames) if d not in IGNORE_NAMES]
        rel_dir = Path(dirpath).relative_to(src)
        target_dir = dst / rel_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        for fn in sorted(filenames):
            if fn in IGNORE_NAMES:
                continue
            s_file = Path(dirpath) / fn
            d_file = target_dir / fn
            if s_file.is_symlink():
                real_src = s_file.resolve()
                if real_src.exists() and real_src.is_file():
                    shutil.copy2(real_src, d_file)
            elif s_file.is_file():
                shutil.copy2(s_file, d_file)


def audit_staged_markdown(root: Path) -> list[str]:
    """Validate staged .md (anti-slop), .py (ast.parse), .json (json.loads), and hidden Unicode."""
    violations: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in sorted(dirnames) if d not in IGNORE_NAMES]
        for fn in sorted(filenames):
            low_fn = fn.lower()
            if not low_fn.endswith((".md", ".py", ".json")):
                continue
            full = Path(dirpath) / fn
            rel = str(full.relative_to(root))
            try:
                text = full.read_text(encoding="utf-8")
            except Exception:
                continue
            if HIDDEN_UNICODE_RE.search(text):
                violations.append(
                    f"{rel}: Security Gate: Hidden zero-width, bidi override, or Unicode Tag character detected."
                )
            if low_fn.endswith(".md"):
                err = lint_markdown_slop(str(full), text)
                if err:
                    violations.append(f"{rel}: {err}")
            elif low_fn.endswith(".py"):
                try:
                    ast.parse(text, filename=rel)
                except SyntaxError as exc:
                    violations.append(f"{rel}: Python SyntaxError at line {exc.lineno}: {exc.msg}")
            elif low_fn.endswith(".json") and text.strip():
                try:
                    json.loads(text)
                except json.JSONDecodeError as exc:
                    violations.append(f"{rel}: JSONDecodeError at line {exc.lineno}: {exc.msg}")
    return violations


def _scrub_trace_string(text: str) -> str:
    cleaned = re.sub(r"/(?:Users|home)/[^/\s\"']+", "/path/to/workspace", text)
    custom_pat = os.environ.get("CLAIRE_SCRUB_PATTERNS", "").strip()
    if custom_pat:
        cleaned = re.sub(custom_pat, "Operator", cleaned, flags=re.IGNORECASE)
    return cleaned


def _collect_recent_failure_traces(days: int = 30, limit: int = 25) -> list[dict[str, str]]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    traces: list[dict[str, str]] = []
    for ws in ALL_WORKSPACE_NAMES:
        log_path = Path(ECOSYSTEM_ROOT) / ws / "state" / "audit_log.jsonl"
        if not log_path.is_file():
            continue
        try:
            with log_path.open("r", encoding="utf-8") as lf:
                for line in lf:
                    raw = line.strip()
                    if not raw:
                        continue
                    try:
                        obj = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    ts = str(obj.get("ts") or "")
                    if ts[:10] < cutoff:
                        continue
                    cat = str(obj.get("category") or "")
                    stat = str(obj.get("status") or "")
                    if stat != "OK" or cat == "PRE_TOOL_BLOCK":
                        traces.append(
                            {
                                "ts": ts[:19],
                                "workspace": ws,
                                "category": cat,
                                "tool": str(obj.get("tool") or ""),
                                "target": _scrub_trace_string(str(obj.get("target") or ""))[:120],
                                "status": _scrub_trace_string(stat)[:160],
                                "summary": _scrub_trace_string(str(obj.get("summary") or ""))[:180],
                            }
                        )
        except OSError:
            continue
    traces.sort(key=lambda x: x["ts"])
    return traces[-limit:]


def cmd_init(args: argparse.Namespace) -> None:
    stage_dir = Path(args.stage_dir).resolve()
    current_dir = stage_dir / "current"
    baseline_dir = stage_dir / "rounds" / "round_0_baseline"

    if stage_dir.exists() and args.reset:
        shutil.rmtree(stage_dir)

    current_dir.mkdir(parents=True, exist_ok=True)
    baseline_dir.mkdir(parents=True, exist_ok=True)

    sources_meta: list[dict[str, str]] = []
    multi_source = len(args.source) > 1
    resolved_sources = [Path(s).resolve() for s in args.source]
    eco_root = Path(ECOSYSTEM_ROOT).resolve()
    basename_counts: dict[str, int] = {}
    for sp in resolved_sources:
        basename_counts[sp.name] = basename_counts.get(sp.name, 0) + 1
    used_rels: set[str] = set()

    for src_path in resolved_sources:
        if not src_path.exists():
            sys.stderr.write(f"Error: source path does not exist: {src_path}\n")
            sys.exit(1)
        rel_to_eco_str: str | None = None
        if multi_source:
            try:
                rel_to_eco_str = str(src_path.relative_to(eco_root))
            except ValueError:
                rel_to_eco_str = None
        if src_path.is_file():
            dest_rel = rel_to_eco_str or src_path.name
            if not rel_to_eco_str and (basename_counts.get(src_path.name, 0) > 1 or dest_rel in used_rels):
                ws_prefix = src_path.parent.name
                dest_rel = f"{ws_prefix}_{src_path.name}"
            used_rels.add(dest_rel)
            copy_filtered_tree(src_path, current_dir / dest_rel)
            copy_filtered_tree(src_path, baseline_dir / dest_rel)
        else:
            dest_rel = (rel_to_eco_str or src_path.name) if multi_source else "."
            if dest_rel != ".":
                used_rels.add(dest_rel)
            dest_curr = current_dir / dest_rel if dest_rel != "." else current_dir
            dest_base = baseline_dir / dest_rel if dest_rel != "." else baseline_dir
            copy_filtered_tree(src_path, dest_curr)
            copy_filtered_tree(src_path, dest_base)
        sources_meta.append({"source_path": str(src_path), "staged_rel": dest_rel})

    baseline_stats = collect_tree_stats(current_dir)
    failure_traces = _collect_recent_failure_traces()
    traces_path = stage_dir / "runtime_failure_traces.json"
    traces_path.write_text(json.dumps(failure_traces, indent=2) + "\n", encoding="utf-8")

    manifest = {
        "initialized_at": datetime.now(timezone.utc).isoformat(),
        "stage_dir": str(stage_dir),
        "current_dir": str(current_dir),
        "runtime_failure_traces_path": str(traces_path),
        "runtime_failure_traces_count": len(failure_traces),
        "current_round": 0,
        "sources": sources_meta,
        "rounds": [
            {
                "round": 0,
                "label": "round_0_baseline",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "summary": "Initial clean baseline mirrored from source.",
                "files_changed_vs_prev": [],
                "files": baseline_stats,
            }
        ],
    }
    manifest_path = stage_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "INITIALIZED",
        "stage_dir": str(stage_dir),
        "current_dir": str(current_dir),
        "file_count": len(baseline_stats),
        "runtime_failure_traces_count": len(failure_traces),
        "runtime_failure_traces_path": str(traces_path),
        "sources": sources_meta,
    }, indent=2))


def cmd_snapshot_round(args: argparse.Namespace) -> None:
    stage_dir = Path(args.stage_dir).resolve()
    manifest_path = stage_dir / "manifest.json"
    if not manifest_path.exists():
        sys.stderr.write(f"Error: manifest.json not found in {stage_dir}. Run `init` first.\n")
        sys.exit(1)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    current_dir = stage_dir / "current"
    _sync_shared_ecosystem_files_in_stage(current_dir)

    if not args.skip_slop_lint:
        violations = audit_staged_markdown(current_dir)
        if violations:
            sys.stderr.write("STAGED MARKDOWN ANTI-SLOP GATE FAILED:\n")
            for v in violations:
                sys.stderr.write(f"  - {v}\n")
            sys.exit(1)

    next_round = int(manifest.get("current_round", 0)) + 1
    round_label = f"round_{next_round}"
    round_dir = stage_dir / "rounds" / round_label
    if round_dir.exists():
        shutil.rmtree(round_dir)
    copy_filtered_tree(current_dir, round_dir)

    curr_stats = collect_tree_stats(current_dir)
    prev_stats = manifest["rounds"][-1]["files"]
    base_stats = manifest["rounds"][0]["files"]

    changed_vs_prev: list[str] = []
    for rel, info in curr_stats.items():
        if rel not in prev_stats or prev_stats[rel]["sha256"] != info["sha256"]:
            changed_vs_prev.append(rel)
    for rel in prev_stats:
        if rel not in curr_stats:
            changed_vs_prev.append(f"DELETED:{rel}")

    changed_vs_base: list[str] = []
    for rel, info in curr_stats.items():
        if rel not in base_stats or base_stats[rel]["sha256"] != info["sha256"]:
            changed_vs_base.append(rel)

    round_entry = {
        "round": next_round,
        "label": round_label,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "summary": args.summary,
        "files_changed_vs_prev": sorted(changed_vs_prev),
        "files_changed_vs_baseline": sorted(changed_vs_base),
        "files": curr_stats,
    }
    manifest["current_round"] = next_round
    manifest["rounds"].append(round_entry)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({
        "status": "SNAPSHOT_RECORDED",
        "round": next_round,
        "snapshot_dir": str(round_dir),
        "current_dir": str(current_dir),
        "files_changed_vs_prev": sorted(changed_vs_prev),
        "files_changed_vs_baseline": sorted(changed_vs_base),
        "slop_lint": "PASS",
    }, indent=2))


def cmd_status(args: argparse.Namespace) -> None:
    stage_dir = Path(args.stage_dir).resolve()
    manifest_path = stage_dir / "manifest.json"
    if not manifest_path.exists():
        sys.stderr.write(f"Error: manifest.json not found in {stage_dir}.\n")
        sys.exit(1)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    current_dir = stage_dir / "current"
    curr_stats = collect_tree_stats(current_dir)
    base_stats = manifest["rounds"][0]["files"]

    modified: list[dict[str, Any]] = []
    for rel, info in curr_stats.items():
        base_info = base_stats.get(rel)
        if not base_info or base_info["sha256"] != info["sha256"]:
            base_tokens = base_info["est_tokens"] if base_info else 0
            modified.append({
                "file": rel,
                "staged_path": str(current_dir / rel),
                "lines": info["lines"],
                "est_tokens": info["est_tokens"],
                "token_delta_vs_baseline": info["est_tokens"] - base_tokens,
            })

    rounds_summary = [
        {
            "round": r["round"],
            "label": r["label"],
            "timestamp": r["timestamp"],
            "summary": r["summary"],
            "files_changed_vs_prev": r.get("files_changed_vs_prev", []),
        }
        for r in manifest.get("rounds", [])
    ]

    print(json.dumps({
        "stage_dir": str(stage_dir),
        "current_dir": str(current_dir),
        "current_round": manifest.get("current_round", 0),
        "sources": manifest.get("sources", []),
        "modified_files_vs_baseline": modified,
        "rounds": rounds_summary,
    }, indent=2))


def cmd_diff(args: argparse.Namespace) -> None:
    stage_dir = Path(args.stage_dir).resolve()
    current_dir = stage_dir / "current"
    baseline_dir = stage_dir / "rounds" / "round_0_baseline"
    if not baseline_dir.exists():
        sys.stderr.write("Error: round_0_baseline not found.\n")
        sys.exit(1)

    curr_stats = collect_tree_stats(current_dir)
    for rel in sorted(curr_stats):
        c_path = current_dir / rel
        b_path = baseline_dir / rel
        try:
            c_lines = c_path.read_text(encoding="utf-8").splitlines(keepends=True)
        except Exception:
            continue
        b_lines = b_path.read_text(encoding="utf-8").splitlines(keepends=True) if b_path.exists() else []
        if c_lines != b_lines:
            diff = difflib.unified_diff(
                b_lines,
                c_lines,
                fromfile=f"baseline/{rel}",
                tofile=f"staged/{rel}",
            )
            sys.stdout.writelines(diff)


def _atomic_copy_file(src_file: Path, dst_file: Path) -> None:
    actual_dst = dst_file.resolve() if dst_file.is_symlink() else dst_file
    actual_dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".iter_apply_", dir=str(actual_dst.parent))
    try:
        with src_file.open("rb") as sf, os.fdopen(fd, "wb") as df:
            shutil.copyfileobj(sf, df)
            df.flush()
            os.fsync(df.fileno())
        shutil.copymode(src_file, tmp_path)
        os.replace(tmp_path, actual_dst)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def _is_manifest_origin_target(
    sources_meta: list[dict[str, Any]],
    target_arg: str,
) -> bool:
    if target_arg in ("origin", "manifest"):
        return True
    target_root = Path(os.path.abspath(target_arg))
    known_roots = {Path(os.path.abspath(ECOSYSTEM_ROOT))}
    for entry in sources_meta:
        known_roots.add(Path(os.path.abspath(entry["source_path"])))
        known_roots.add(Path(entry["source_path"]).resolve())
    return bool(sources_meta and (target_root in known_roots or target_root.resolve() in known_roots))


def _resolve_apply_destination(
    rel: str,
    sources_meta: list[dict[str, Any]],
    target_arg: str,
) -> Path:
    """Resolve a staged relative path back to its origin source_path or custom target directory."""
    exact_map: dict[str, Path] = {}
    prefix_map: list[tuple[str, Path]] = []
    root_source: Path | None = None

    for entry in sources_meta:
        src_p = Path(os.path.abspath(entry["source_path"]))
        s_rel = entry["staged_rel"]
        if s_rel == ".":
            root_source = src_p
        elif src_p.is_file() or src_p.is_symlink() or not (src_p.exists() and src_p.is_dir()):
            exact_map[s_rel] = src_p
        else:
            prefix_map.append((s_rel, src_p))

    if _is_manifest_origin_target(sources_meta, target_arg):
        if rel in exact_map:
            return exact_map[rel]
        for s_rel, src_p in prefix_map:
            if rel == s_rel:
                return src_p
            if rel.startswith(s_rel + "/"):
                return src_p / rel[len(s_rel) + 1 :]
        if root_source is not None:
            return root_source / rel

    target_root = Path(target_arg).resolve()
    return target_root / rel if target_root.is_dir() or not target_root.exists() else target_root


def _resolve_fanout_destinations(
    rel: str,
    sources_meta: list[dict[str, Any]],
    fanout_map: dict[str, list[str]],
    target_arg: str,
) -> list[Path]:
    if not _is_manifest_origin_target(sources_meta, target_arg):
        return []
    out: list[Path] = []
    for dst_str in fanout_map.get(rel) or []:
        p = Path(dst_str).resolve()
        if p not in out:
            out.append(p)
    for entry in sources_meta:
        s_rel = str(entry.get("staged_rel") or "")
        ft_list = entry.get("fanout_targets") or []
        if not isinstance(ft_list, list) or not ft_list:
            continue
        if rel == s_rel:
            for ft in ft_list:
                p = Path(str(ft)).resolve()
                if p not in out:
                    out.append(p)
        elif rel.startswith(s_rel + "/"):
            sub_rel = rel[len(s_rel) + 1 :]
            for ft in ft_list:
                p = (Path(str(ft)).resolve() / sub_rel).resolve()
                if p not in out:
                    out.append(p)
    for shared_rel in SHARED_ECOSYSTEM_REL_PATHS:
        if rel == shared_rel or rel == f"claire-architect/{shared_rel}":
            for ws in ALL_WORKSPACE_NAMES:
                p = (Path(ECOSYSTEM_ROOT) / ws / shared_rel).resolve()
                if p.parent.is_dir() and p not in out:
                    out.append(p)
    return out


def cmd_apply(args: argparse.Namespace) -> None:
    stage_dir = Path(args.stage_dir).resolve()
    current_dir = stage_dir / "current"
    baseline_dir = stage_dir / "rounds" / "round_0_baseline"
    manifest_path = stage_dir / "manifest.json"

    if not current_dir.exists() or not baseline_dir.exists():
        sys.stderr.write(f"Error: staging directories missing inside {stage_dir}.\n")
        sys.exit(1)

    sources_meta: list[dict[str, Any]] = []
    fanout_map: dict[str, list[str]] = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            sources_meta = manifest.get("sources", [])
            fanout_map = manifest.get("fanout_map", {}) or {}
        except Exception:
            sources_meta = []
            fanout_map = {}

    violations = audit_staged_markdown(current_dir)
    if violations:
        sys.stderr.write("STAGED TREE INTEGRITY / ANTI-SLOP GATE FAILED ON APPLY:\n")
        for v in violations:
            sys.stderr.write(f"  - {v}\n")
        sys.exit(1)

    curr_stats = collect_tree_stats(current_dir)
    base_stats = collect_tree_stats(baseline_dir)

    to_copy: list[str] = []
    for rel, info in curr_stats.items():
        if args.all_files or rel not in base_stats or base_stats[rel]["sha256"] != info["sha256"]:
            to_copy.append(rel)

    deleted_rels = [
        rel
        for rel in sorted(set(base_stats.keys()) - set(curr_stats.keys()))
        if not (rel == "vault_staging" or rel.startswith("vault_staging/"))
    ]

    applied: list[str] = []
    seen_applied: set[Path] = set()
    vault_staging_files: list[str] = []
    for rel in sorted(to_copy):
        if rel == "vault_staging" or rel.startswith("vault_staging/"):
            vault_staging_files.append(rel)
            continue
        src_file = current_dir / rel
        primary_dst = _resolve_apply_destination(rel, sources_meta, args.target)
        fanout_dsts = _resolve_fanout_destinations(rel, sources_meta, fanout_map, args.target)
        for dst_file in [primary_dst, *[p for p in fanout_dsts if p != primary_dst]]:
            resolved_dst = dst_file.resolve()
            if resolved_dst in seen_applied:
                continue
            seen_applied.add(resolved_dst)
            if not args.dry_run:
                _atomic_copy_file(src_file, dst_file)
            applied.append(str(dst_file))

    removed: list[str] = []
    for rel in deleted_rels:
        dst_file = _resolve_apply_destination(rel, sources_meta, args.target)
        if dst_file.is_file():
            if not args.dry_run:
                dst_file.unlink()
            removed.append(str(dst_file))

    print(json.dumps({
        "status": "DRY_RUN" if args.dry_run else "APPLIED",
        "target": args.target if args.target in ("origin", "manifest") else str(Path(args.target).resolve()),
        "files_count": len(applied),
        "files": applied,
        "removed_count": len(removed),
        "removed_files": removed,
        "vault_staging_files": vault_staging_files,
    }, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Isolated staging & round snapshot manager for /iterate."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Initialize staging sandbox from source file(s) or folder(s).")
    p_init.add_argument("--stage-dir", required=True, help="Staging directory path (e.g., <artifact_dir>/scratch/iterate_stage).")
    p_init.add_argument("--source", action="append", required=True, help="Source file or directory to mirror (repeatable).")
    p_init.add_argument("--reset", action="store_true", help="Wipe existing stage-dir before initializing.")
    p_init.set_defaults(func=cmd_init)

    p_snap = sub.add_parser("snapshot-round", help="Validate anti-slop gate and snapshot current staged files as Round N.")
    p_snap.add_argument("--stage-dir", required=True, help="Staging directory path.")
    p_snap.add_argument("--summary", required=True, help="One-line summary of changes staged in this round.")
    p_snap.add_argument("--skip-slop-lint", action="store_true", help="Skip human-voice.md anti-slop check on staged .md files.")
    p_snap.set_defaults(func=cmd_snapshot_round)

    p_stat = sub.add_parser("status", help="Show current round, modified files vs baseline, and round history.")
    p_stat.add_argument("--stage-dir", required=True, help="Staging directory path.")
    p_stat.set_defaults(func=cmd_status)

    p_diff = sub.add_parser("diff", help="Print unified diff between baseline and current staged files.")
    p_diff.add_argument("--stage-dir", required=True, help="Staging directory path.")
    p_diff.set_defaults(func=cmd_diff)

    p_apply = sub.add_parser("apply", help="Apply modified staged files to a target directory or file on explicit user request.")
    p_apply.add_argument("--stage-dir", required=True, help="Staging directory path.")
    p_apply.add_argument("--target", required=True, help="Target root directory or file path.")
    p_apply.add_argument("--all-files", action="store_true", help="Copy all staged files, not only modified ones.")
    p_apply.add_argument("--dry-run", action="store_true", help="Preview files that would be applied without writing.")
    p_apply.set_defaults(func=cmd_apply)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
