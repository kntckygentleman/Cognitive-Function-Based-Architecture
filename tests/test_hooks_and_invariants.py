#!/usr/bin/env python3
"""Automated verification suite for the Cognitive Function Based Agent Harness.

Tests:
1. PreToolUse Core Architecture Lock denies unauthorized edits to locked files.
2. PreToolUse Core Architecture Lock allows edits when [Claire Debug Mode] is present.
3. PreToolUse Anti-Slop Gate rejects em dashes, en dashes, [WEIGHT: ...] tags,
   negative-positive contrast frames, and banned AI filler vocabulary in .md writes.
4. PreToolUse checks ReplacementChunks and blocks invisible Unicode / bidi payloads.
5. Calendar flag parsing and secondary-calendar Optimistic Concurrency Control (OCC) helpers.
6. Kortex bulk schema normalization across 'calls' and 'operations' schemas.
7. Flat SessionStart handler schema in .agents/hooks.json.
8. PreToolUse Beeper SMS Gate blocks direct MCP send_message calls.
9. PreToolUse Kortex MCP Rewriter injects citation_format="none" and default account.
10. SessionStart State Auditor passes clean with CLAIRE_REFERENCE_DATE and flags >100-line files.
11. Persona header schema and <scope>-<mbti> Council/critic panel congruence passes cleanly.
12. Repository-wide Markdown, Python, & JSON audit verifies zero slop, zero hidden Unicode,
    zero non-RFC-4122 fixture UUIDs, zero non-placeholder emails/calendar IDs, and zero host paths.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(REPO_ROOT, ".agents/scripts")
CORE_HOOKS_PATH = os.path.join(SCRIPTS_DIR, "core_hooks.py")
HOOKS_PATH = os.path.join(SCRIPTS_DIR, "claire_hooks.py")

core_spec = importlib.util.spec_from_file_location("core_hooks", CORE_HOOKS_PATH)
core_hooks = importlib.util.module_from_spec(core_spec)
assert core_spec and core_spec.loader
core_spec.loader.exec_module(core_hooks)

spec = importlib.util.spec_from_file_location("claire_hooks", HOOKS_PATH)
claire_hooks = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(claire_hooks)

UUID_V4_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
EMAIL_OR_CAL_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
)
ALLOWED_PLACEHOLDER_ADDRESSES = frozenset(
    {
        "operator@example.com",
        "user@example.com",
        "shared-team@group.calendar.google.com",
    }
)
HOST_HOME_PATH_RE = re.compile(r"/(?:Users|home)/[A-Za-z0-9._-]+")
NON_UTC_OFFSET_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-](?!00:00)\d{2}:\d{2}")


class TestHooksAndInvariants(unittest.TestCase):
    def tearDown(self) -> None:
        pre_hash_p = os.path.join(REPO_ROOT, "state/.pre_tool_hashes.json")
        if os.path.exists(pre_hash_p):
            try:
                os.remove(pre_hash_p)
            except OSError:
                pass

    def test_pre_tool_use_denies_locked_file_without_debug_mode(self) -> None:
        locked_target = os.path.join(REPO_ROOT, ".agents/hooks.json")
        payload = {
            "lastUserInput": "Please update the hook timeout.",
            "toolCall": {
                "name": "edit_file",
                "args": {"Path": locked_target, "Replacement": "{}"},
            },
        }
        res = claire_hooks.handle_pre_tool_use(payload)
        self.assertEqual(res.get("decision"), "deny")
        self.assertIn("Core Architecture Lock", res.get("reason", ""))

    def test_pre_tool_use_allows_locked_file_with_debug_mode(self) -> None:
        locked_target = os.path.join(REPO_ROOT, ".agents/hooks.json")
        payload = {
            "lastUserInput": "[Claire Debug Mode] Update the hook timeout.",
            "toolCall": {
                "name": "edit_file",
                "args": {"Path": locked_target, "Replacement": "{}"},
            },
        }
        res = claire_hooks.handle_pre_tool_use(payload)
        self.assertEqual(res.get("decision"), "allow")

    def test_pre_tool_use_anti_slop_gate_rejects_violations(self) -> None:
        unlocked_md = os.path.join(REPO_ROOT, "state/active_sprint.md")
        bad_payloads = [
            "This line has an em dash \u2014 which is banned.",
            "Rule with fake weight [WEIGHT: 0.95] should fail.",
            "It's not a prompt, it's a control loop.",
            "Let us delve into this seamless paradigm.",
        ]
        for text in bad_payloads:
            payload = {
                "lastUserInput": "Update active sprint",
                "toolCall": {
                    "name": "write_file",
                    "args": {"Path": unlocked_md, "Content": text},
                },
            }
            res = claire_hooks.handle_pre_tool_use(payload)
            self.assertEqual(
                res.get("decision"),
                "deny",
                msg=f"Expected anti-slop denial for payload: {text}",
            )

    def test_pre_tool_use_checks_replacement_chunks_and_hidden_unicode(self) -> None:
        unlocked_md = os.path.join(REPO_ROOT, "state/active_sprint.md")
        chunk_payload = {
            "lastUserInput": "Update active sprint",
            "toolCall": {
                "name": "edit_file",
                "args": {
                    "Path": unlocked_md,
                    "ReplacementChunks": [{"ReplacementContent": "Slop em dash \u2014 in chunk."}],
                },
            },
        }
        res = claire_hooks.handle_pre_tool_use(chunk_payload)
        self.assertEqual(res.get("decision"), "deny")

        unicode_payload = {
            "lastUserInput": "Update active sprint",
            "toolCall": {
                "name": "write_file",
                "args": {"Path": unlocked_md, "Content": "Hidden zero-width \u200b character."},
            },
        }
        res_u = claire_hooks.handle_pre_tool_use(unicode_payload)
        self.assertEqual(res_u.get("decision"), "deny")

    def test_calendar_flag_and_secondary_occ_helpers(self) -> None:
        self.assertTrue(claire_hooks._gcal_targets_shared("gcalendar create --cal shared --title 'Sync'"))
        self.assertTrue(claire_hooks._gcal_reads_secondary("gcalendar list --cal work"))
        self.assertFalse(
            claire_hooks._gcal_reads_secondary("gcalendar list --cal shared"),
            msg="Reading Shared calendar alone must not satisfy secondary calendar OCC check.",
        )

    def test_kortex_bulk_schema_normalization_and_mutation_tools(self) -> None:
        self.assertIn("set_source_note", claire_hooks.KORTEX_MUTATION_TOOLS)
        calls_schema = {
            "tool": "update_notebooklm_note",
            "calls": [{"notebook_id": "nb-1", "note_id": "n-1", "content": "ok"}],
        }
        ops_schema = {
            "operations": [{"tool": "set_source_note", "args": {"source_id": "s-1", "note": "tag"}}]
        }
        self.assertEqual(len(claire_hooks.normalize_kortex_bulk_ops(calls_schema)), 1)
        self.assertEqual(claire_hooks.normalize_kortex_bulk_ops(calls_schema)[0][0], "update_notebooklm_note")
        self.assertEqual(claire_hooks.normalize_kortex_bulk_ops(ops_schema)[0][0], "set_source_note")

    def test_hooks_json_flat_session_start_schema(self) -> None:
        hooks_json_path = os.path.join(REPO_ROOT, ".agents/hooks.json")
        with open(hooks_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for group_name, cfg in data.items():
            self.assertTrue(cfg.get("enabled"), msg=f"{group_name} must set enabled: true")
            for handler in cfg.get("SessionStart", []):
                self.assertEqual(handler.get("type"), "command", msg="SessionStart must be a flat handler list")
                self.assertNotIn("matcher", handler, msg="SessionStart handlers must not be nested in matcher blocks")

    def test_pre_tool_use_blocks_direct_beeper_mcp_send(self) -> None:
        payload = {
            "lastUserInput": "Reply to that text",
            "toolCall": {
                "name": "call_mcp_tool",
                "args": {
                    "ServerName": "beeper",
                    "ToolName": "send_message",
                    "Arguments": {"chatID": "123", "text": "Hello"},
                },
            },
        }
        res = claire_hooks.handle_pre_tool_use(payload)
        self.assertEqual(res.get("decision"), "deny")
        self.assertIn("Outbound SMS/RCS Gate", res.get("reason", ""))

    def test_pre_tool_use_rewrites_kortex_ask_question(self) -> None:
        payload = {
            "lastUserInput": "Check the vault",
            "toolCall": {
                "name": "call_mcp_tool",
                "args": {
                    "ServerName": "kortex",
                    "ToolName": "ask_question",
                    "Arguments": {"notebook_id": "nb-1", "question": "Status?"},
                },
            },
        }
        res = claire_hooks.handle_pre_tool_use(payload)
        self.assertEqual(res.get("decision"), "allow")
        rewritten = res.get("overwrite", {}).get("Arguments", {})
        self.assertEqual(rewritten.get("citation_format"), "none")
        self.assertTrue(rewritten.get("account"))

    def test_session_start_auditor_passes_with_reference_date_and_flags_line_cap(self) -> None:
        prev_ref = os.environ.get("CLAIRE_REFERENCE_DATE")
        try:
            os.environ["CLAIRE_REFERENCE_DATE"] = "2026-11-09"
            clean_res = claire_hooks.handle_session_start({})
            self.assertEqual(clean_res, {}, msg=f"Expected clean SessionStart, got: {clean_res}")
        finally:
            if prev_ref is None:
                os.environ.pop("CLAIRE_REFERENCE_DATE", None)
            else:
                os.environ["CLAIRE_REFERENCE_DATE"] = prev_ref

        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as tmp:
            tmp.write("**Last Updated:** 2099-01-01\n" + ("line\n" * 105))
            tmp_path = tmp.name
        orig = list(claire_hooks.STATE_FILES_TO_AUDIT)
        try:
            claire_hooks.STATE_FILES_TO_AUDIT = [("temp_state.md", tmp_path, 100)]
            res = claire_hooks.handle_session_start({})
            self.assertIn("injectSteps", res)
            self.assertIn("exceeds 100-line cap", res["injectSteps"][0]["ephemeralMessage"])
        finally:
            claire_hooks.STATE_FILES_TO_AUDIT = orig
            os.remove(tmp_path)

    def test_persona_and_panel_congruence(self) -> None:
        issues = core_hooks.validate_persona_and_panel_congruence(REPO_ROOT)
        self.assertEqual(issues, [], msg=f"Persona/panel congruence violations: {issues}")

    def test_zero_config_cli_subcommands_run_cleanly_on_standalone_clone(self) -> None:
        env = dict(os.environ)
        env.pop("CLAIRE_REFERENCE_DATE", None)
        env.pop("CLAIRE_WORKSPACE_ROOT", None)
        env.pop("CLAIRE_ECOSYSTEM_ROOT", None)

        ss = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, "claire_hooks.py"), "SessionStart"],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(json.loads(ss.stdout.strip()), {})

        for subcmd in (
            ["tokens", "--format", "json"],
            ["verify", "-a"],
            ["epistemic", "--check", "--format", "json"],
            ["summary"],
            ["query"],
            ["changelog"],
        ):
            proc = subprocess.run(
                [sys.executable, os.path.join(SCRIPTS_DIR, "audit_log.py"), *subcmd],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                proc.returncode,
                0,
                msg=f"audit_log.py {' '.join(subcmd)} failed: {proc.stderr or proc.stdout}",
            )
            if subcmd[0] == "tokens":
                tok_data = json.loads(proc.stdout)
                self.assertEqual(tok_data.get("violations"), [])
                for w_row in tok_data.get("workspaces", []):
                    self.assertGreater(
                        w_row.get("prompt_tokens", 0),
                        0,
                        msg=f"Expected non-zero prompt_tokens for {w_row}",
                    )
                    self.assertGreater(
                        w_row.get("state_tokens", 0),
                        0,
                        msg=f"Expected non-zero state_tokens for {w_row}",
                    )
                total_toks = tok_data.get("total_turn0_tokens", 0)
                with open(os.path.join(REPO_ROOT, "FRAMEWORK_CHANGELOG.md"), "r", encoding="utf-8") as f:
                    changelog_md = f.read()
                with open(os.path.join(REPO_ROOT, "README.md"), "r", encoding="utf-8") as f:
                    readme_md = f.read()
                self.assertIn(
                    f"**~{total_toks:,} tokens**",
                    changelog_md,
                    msg="FRAMEWORK_CHANGELOG.md ecosystem total must match audit_log.py tokens output",
                )
                self.assertIn(
                    f"**~{total_toks:,} Turn-0 tokens**",
                    readme_md,
                    msg="README.md Turn-0 token total must match audit_log.py tokens output",
                )

    def test_all_tracked_files_are_slop_and_pii_free(self) -> None:
        custom_pat = os.environ.get("CLAIRE_BANNED_PII_REGEX", "").strip()
        custom_re = re.compile(custom_pat, re.IGNORECASE) if custom_pat else None
        for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
            dirnames[:] = [d for d in dirnames if d not in (".git", "__pycache__")]
            for fn in filenames:
                if fn.startswith(".pre_tool_hashes") or not fn.endswith((".md", ".py", ".json", ".jsonl")):
                    continue
                full_path = os.path.join(dirpath, fn)
                with open(full_path, "r", encoding="utf-8") as f:
                    content = f.read()
                for uid in UUID_V4_RE.findall(content):
                    self.assertTrue(
                        uid.lower().startswith("00000000-0000-4000-8000-000000000"),
                        msg=f"Non-fixture UUID detected in {full_path}: '{uid}'",
                    )
                for addr in EMAIL_OR_CAL_RE.findall(content):
                    self.assertIn(
                        addr.lower(),
                        ALLOWED_PLACEHOLDER_ADDRESSES,
                        msg=f"Non-placeholder email or calendar ID detected in {full_path}: '{addr}'",
                    )
                self.assertIsNone(
                    HOST_HOME_PATH_RE.search(content),
                    msg=f"Host user directory path detected in {full_path}",
                )
                self.assertIsNone(
                    NON_UTC_OFFSET_RE.search(content),
                    msg=f"Non-UTC timezone offset detected in {full_path}",
                )
                if custom_re:
                    self.assertIsNone(
                        custom_re.search(content),
                        msg=f"Custom banned PII pattern matched in {full_path}",
                    )
                if fn.endswith(".md"):
                    slop_err = claire_hooks.lint_markdown_slop(full_path, content)
                    self.assertIsNone(
                        slop_err,
                        msg=f"Slop violation in {full_path}: {slop_err}",
                    )


if __name__ == "__main__":
    unittest.main()
