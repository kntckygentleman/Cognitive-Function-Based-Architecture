# ANTIGRAVITY AGENT HARNESS & TOKEN TELEMETRY CHANGELOG
**Scope:** Multi-Workspace Cognitive-Function Agent Harness (`claire-architect`, `elmer`, `chad`, `andy`, `grant`)  
**Last Updated:** 2026-11-09 | **Schedule:** Weekly Automated Audit (Sundays 09:00 AM via `audit_log.py tokens`)

> **Directive for Weekly Review Runs:** Run `python3 .agents/scripts/audit_log.py tokens` at Step 1 of every weekly review before scanning workspaces. Compare live Turn-0 token loads against the **Current Verified Baseline** below to measure week-over-week drift, never re-propose already completed optimizations, and append a dated changelog entry after operator-approved changes are applied.

---

## 1. Current Verified Token Telemetry Baseline (`2026-11-09` Post-Kernel Decoupling & `<scope>-<mbti>` Standardization)

*Token estimates are computed deterministically by `audit_log.py tokens` (~3.8 characters per token).*

| Workspace | Primary Prompt (`GEMINI.md`) | `human-voice.md` | State Hydration (`@` Expanded) | Turn-0 Always-On Total | Dedicated UUID Manifest / Shelf Location |
| :--- | :---: | :---: | :---: | :---: | :--- |
| **Claire** (`claire-architect`) | 10,102 c (~2,658 t) | 3,560 c (~937 t) | 7,625 c (~2,007 t) | **~5,602 tokens** | `state/ecosystem_registry.md + core_hooks.py` |
| **Elmer** (`elmer`) | 5,733 c (~1,509 t) | 3,560 c (~937 t) | 14,504 c (~3,817 t) | **~6,263 tokens** | `state/vault_manifest.json + elmer_hooks.py` |
| **Chad** (`chad`) | 5,278 c (~1,389 t) | 3,560 c (~937 t) | 6,412 c (~1,687 t) | **~4,013 tokens** | `state/vault_manifest.json + active_protocol_and_labs.md` |
| **Andy** (`andy`) | 4,418 c (~1,163 t) | 3,560 c (~937 t) | 5,650 c (~1,487 t) | **~3,587 tokens** | `state/style_and_catalog_dna.md + active_setlist.md` |
| **Grant** (`grant`) | 6,686 c (~1,759 t) | 3,560 c (~937 t) | 4,361 c (~1,148 t) | **~3,844 tokens** | `GRANT_PROMPT.md + grant-paper-engine/SKILL.md` |
| **ECOSYSTEM TOTAL** | **~8,478 t** | **~4,685 t** | **~10,146 t** | **~23,309 tokens** | **100% UUIDs & Shelves Accounted For** |

---

## 2. Non-Negotiable Harness Invariants (Never Strip on Future Runs)

1. **Counterweight Control Loops & Precedence Rule:** Never compress or alter the five workspace counterweight priors (`Claire INTJ 5w6/1w9`, `Elmer ISTJ 6w5`, `Chad ISTP 5w6`, `Andy ESTP 7w8`, `Grant INTP 5w6`), the `Observed Reality > Typology Heuristic` precedence rule, or the Veil of Ignorance delegation clause.
2. **Hook Locks, Anti-Slop Gate, & Debug Gates:** Never weaken `LOCKED_PATHS`, `lint_markdown_slop`, `lint_security_payload`, the Beeper outbound MCP block, or `[<Agent> Debug Mode]` checks in `core_hooks.py` and `*_hooks.py`.
3. **Zero UUID or Drive Shelf Erasure:** Never delete a NotebookLM `notebook_id`, canonical `source_id`, Studio `note_id`, or Google Drive folder ID from Turn-0 hydration unless it is first persisted in a local `state/vault_manifest.json` or `SKILL.md`.
4. **Domain Thresholds:** Preserve all clinical cutoffs (`<=180d` bloodwork, `28d` perishable stability, 4-block daily protein floor), two-stage statistical gates (`60/40` IS/OOS split, Stage 1 `N>=25` falsification screen, Stage 2 `N>=100` + Deflated Sharpe gate, `OOS PF>=1.25`, `2 bps` slippage, fixed-fractional `$1R` paper portfolio sizing), tabletop mechanics (`2024 5e` DCs, faction heat clocks, `1.00 in` grid), and musical specs (`EADGBE` vocal reference grid rule and automatic `-3rd` upper-register harmony inversion).
5. **Python 3.9 Compatibility:** All `.py` scripts must remain compatible with macOS system Python 3.9.6 (use `from __future__ import annotations` whenever PEP 604 `X | Y` union type hints are used).

---

## 3. Running Weekly Changelog

### [2026-11-09] 5-Workspace Kernel Extraction (`core_hooks.py`), `audit_log.py`, `/iterate`, & `<scope>-<mbti>` Council Standardization
- **Shared Kernel Extraction (`core_hooks.py`):** Extracted universal security, invisible-Unicode/prompt-injection defense, anti-slop linting, CaMeL taint decay, Kortex token defaults, and SHA-256 hash-chained audit logging into `.agents/scripts/core_hooks.py`, cutting `claire_hooks.py` by 68% and eliminating subprocess overhead across specialist workspaces.
- **Zero-Token Provenance & Bitemporal Epistemic CLI (`audit_log.py`):** Added on-demand provenance query, SHA-256 chain verification, ADR recording, bitemporal Vault/L1 congruence auditing (`epistemic`), and Turn-0 token telemetry (`tokens`).
- **Adversarial Staged Iteration (`/iterate`) & `<scope>-<mbti>` Critic Standardization:** Added `.agents/skills/iterate` (`iterate_stage.py` + `critic_panels.md`) and standardized all 19 Council and domain critic seats on `<scope>-<mbti>` (`council-enfj`, `council-istj`, `council-isfp`, `Devil's Advocate`), enforced by `validate_persona_and_panel_congruence`.

### [2026-11-08] `/council` Anti-Slop Audit, Preventive `PreToolUse` Linter, & `beeper-sms-triage` Integration
- **Preventive Hook Engineering:** Added `lint_markdown_slop` to `PreToolUse` across all five workspaces' `*_hooks.py` scripts to reject Markdown writes containing em/en dashes, unparsed bracketed weight tags, negative-positive contrast formulas, or banned AI filler vocabulary before touching disk.
- **Outbound SMS/RCS Gate:** Added deterministic `PreToolUse` block in `claire_hooks.py` preventing direct `beeper` MCP `send_message` calls from bypassing `beeper_triage.py stage-reply` -> `send-staged`.
- **Two-Stage Statistical Funnel (`grant`):** Distinguished Stage 1 early falsification (`N >= 25` OOS trades, `t-stat < 1.0`) from Stage 2 statistical power (`N >= 100` cumulative trades + Model Graveyard Deflated Sharpe).

### [2026-11-02] Initial Cross-Workspace Compaction & Hook Standardization (Baseline Build)
- Compacted primary workspace prompts (`claire-architect`, `elmer`, `andy`, `chad`) and standardized native Antigravity `.agents/hooks.json` (`SessionStart` line/staleness audits + `PreToolUse` `[<Agent> Debug Mode]` locks and Kortex `citation_format: "none"` auto-rewrites).
