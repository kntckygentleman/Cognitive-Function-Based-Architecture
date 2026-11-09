# ECOSYSTEM REGISTRY (VAULT + COMPUTE ENGINE TOPOLOGY)
**Last Updated:** 2026-11-09 | **Epistemic Check:** `audit_log.py epistemic` | **Line Cap:** 100

## 1. Hardware & Workspace Topology
- **Primary Compute Host (Local Engine):** Hosts five isolated Antigravity workspaces:
  1. **Claire (`/path/to/antigravity/claire-architect`):** `INTJ 5w6/1w9` meta-architecture, schedule orchestration, and Vault governance.
  2. **Elmer (`/path/to/antigravity/elmer`):** Standalone `ISTJ 6w5` D&D 5e (`2024`) & sci-fi tabletop workspace. Owns tabletop skills, 1.00-inch PDF battle maps, handouts, and Hot/Warm Kortex Vault state hydration.
  3. **Andy (`/path/to/antigravity/andy`):** Standalone `ESTP 7w8` songwriting, harmony, and chart studio workspace. Owns `andy-song-lab` and synced song charts.
  4. **Chad (`/path/to/antigravity/chad`):** Standalone `ISTP 5w6` sports physiology, hypertrophy, and biomarker workspace. Owns `chad-telemetry-sync`, `chad-protocol-publisher`, and `Biometrics` vault state hydration.
  5. **Grant (`/path/to/antigravity/grant`):** Standalone `INTP 5w6` quantitative research, 60/40 Out-of-Sample backtesting, and automated paper-trading workspace. Owns `grant-paper-engine`, reference paper ledger (`data/paper_ledger.json`), official broker OpenAPI sandbox bridge, and `Quant Lab` vault state hydration.
- **Isolated Production Host (Regulated Operations):** Dedicated air-gapped environment with zero personal AI or Kortex tooling.

## 2. NotebookLM Vault Instances (7 Active Notebooks via Kortex MCP)
*Rule:* Pass `notebook_id` and `source_id` directly to `get_source_content`. Domain-internal sources and Studio Notes are tracked inside their respective domain workspaces (`elmer`, `andy`, `chad`, `grant`).
- **Orchestrator Architecture Vault (`00000000-0000-4000-8000-000000000100`, Owned by `claire-architect`):**
  - `01_Operator_And_Counterweight_Spec.md` (`00000000-0000-4000-8000-000000000101`)
  - `02_Active_Operations_And_Sprint_Protocols.md` (`00000000-0000-4000-8000-000000000102`)
  - `03_Shared_Calendar_And_Comms_Governance.md` (`00000000-0000-4000-8000-000000000103`)
  - `04_Chronological_Changelog_Directives.md` (`00000000-0000-4000-8000-000000000104`)
- **Media Storyboard Vault (`00000000-0000-4000-8000-000000000200`, Owned by `claire-architect`):**
  - `00_STATE_Media_Production_Spec.md` (`00000000-0000-4000-8000-000000000201`)
- **Biometrics Vault (`00000000-0000-4000-8000-000000000300`, Owned by `chad`).**
- **Campaign Primary Vault (`00000000-0000-4000-8000-000000000400`, Owned by `elmer`).**
- **Campaign Sci-Fi Vault (`00000000-0000-4000-8000-000000000401`, Owned by `elmer`).**
- **Tabletop Rules Vault (`00000000-0000-4000-8000-000000000402`, Owned by `elmer`).**
- **Quant Lab Vault (`00000000-0000-4000-8000-000000000500`, Owned by `grant`).**

## 3. Shared Pipelines & Drive I/O Quarantine
- **Kortex MCP Bridge:** `~/.gemini/config/mcp_config.json` (Pro tier).
- **Beeper Desktop SMS/RCS Bridge:** `http://127.0.0.1:23373/v0/mcp` + `beeper_desktop_api` v6.0.0. Outbound messages gated by `.agents/skills/beeper-sms-triage` (`stage-reply` -> `send-staged`) and `claire_hooks.py`.
- **Google Drive Inbound Drop (`00_Antigravity_Inbox`, `<DRIVE_FOLDER_INBOX>`):** Strictly for untrusted/raw human inputs and third-party exports.
- **Google Drive Outbound Shelf (`00_Antigravity_Outputs`, `<DRIVE_FOLDER_OUTPUTS>`):** Strictly for verified engine-to-human deliverables (`Tabletop`, `Health`, `Music`, `Quant`).
- **Local Harness & Archive Stack:** `core_hooks.py` + `claire_hooks.py`, `audit_log.py` (`query|summary|changelog|verify|record-adr|epistemic|tokens`), `/council`, and `/iterate`.
