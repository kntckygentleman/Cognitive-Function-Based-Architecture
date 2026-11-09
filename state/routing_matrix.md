# GOOGLE AI ULTRA TOOL-ROUTING MATRIX (VAULT + ENGINE MODEL)
**Last Updated:** 2026-11-09 | **Epistemic Check:** `audit_log.py epistemic` | **Line Cap:** 100

- **Google Antigravity (Primary Compute Host):** Multi-step custom agents, local execution, deterministic `.agents/hooks.json` enforcement, and writing canonical `00_STATE_` updates to the NotebookLM Vault.
- **Google Drive (`gdrive` CLI):** Strict I/O separation (Inbound `00_Antigravity_Inbox` vs Outbound `00_Antigravity_Outputs` per `ecosystem_registry.md`).
- **NotebookLM via Kortex MCP (Persistent SSoT Vault):** Canonical `00_STATE_` reads first via `get_source_content`; `citation_format: "none"`; wrap >3 calls in `bulk`.
- **Gemini 4 Argon / Deep Think:** Strategic trade-offs and single-shot architectural reasoning.
- **Visual Deep Research (200/day):** Vetting external APIs and technical benchmarks before building.
- **Exogenous Source Authority Waterfall (`EXTERNAL_LOOKUP`):**
  1. **Runtime Conformance (`EXEC_CMD`):** Local `python3 -c` stdlib/module introspection and passing smoke tests outrank all prose claims.
  2. **Tier 1 (`TIER_1_UPSTREAM_SPEC`, Binding Specs):** Installed `.gemini/` skills (`agy-customizations`, `antigravity-guide`, `gemini-api-dev`, `retrieving-developer-knowledge`), `gemini_search_docs` / `gemini_get_doc`, and official spec domains (`docs.python.org`, `ai.google.dev`, `cloud.google.com`, `w3.org`, `rfc-editor.org`, `modelcontextprotocol.io`).
  3. **Tier 2 (`TIER_2_EMPIRICAL_PRACTITIONER`, Primary Repos & Patterns):** Primary maintainer repositories (`github.com/getzep/graphiti`, `letta-ai/letta`, `All-Hands-AI/OpenHands`, `stanford-oval/storm`, `upstash/context7`, `llmstxt.org`) and empirical practitioners (`simonwillison.net`, `eugeneyan.com`, `hamel.dev`, `shreyashankar.com`, `lilianweng.github.io`, `huyenchip.com`, `anthropic.com`).
  4. **Tier 3 (`TIER_3_UNVERIFIED_WEB`, Advisory Only):** General `search_web` results require Tier-1 spec or runtime conformance verification (`CRAG` gate) before mutating `CODE_SYSTEM` files.
- **Jules (Async Coding Agent):** Scoped repository refactoring and async GitHub PRs.
- **Google Colab & AI Studio ($100/mo Cloud Credits):** Contained `[SANDBOX]` Python and `google-genai` experiments.
- **Flow / Veo 3.1 / Genie:** Visual storyboarding and atomic scene generation.
