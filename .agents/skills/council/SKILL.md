---
name: council
description: >-
  Convene the 7-seat Impartial Adversarial Agent Council via /council (or
  wing/seat filters) under a strict Veil of Ignorance across Rational (INTJ,
  INTP, ENTJ), Human/Somatic (ENFJ, ISTJ, ISFP), and 0.5x Devil's Advocate (ENTP 8w7) seats.
---

# Impartial Adversarial Council Protocol (`/council`)

1. **Seat Selection:**
   - `/council` (default): All 7 seats (`council-intj`, `council-intp`, `council-entj`, `council-enfj`, `council-istj`, `council-isfp` at `1.0x` + `council-entp` at `0.5x`).
   - `/council rational`: `council-intj`, `council-intp`, `council-entj`, `council-entp` (`0.5x`).
   - `/council human`: `council-enfj`, `council-istj`, `council-isfp`, `council-entp` (`0.5x`).
   - `/council <seat>`: Named seat(s) only (`intj`, `intp`, `entj`, `enfj`, `istj`, `isfp`, `entp`).
   - Read [personas.md](./references/personas.md) on demand for the selected `SystemPrompt` blocks.

2. **Anonymized Bailiff Brief (`<150` Words, Zero Subagent Kortex Calls):**
   - If Vault facts are needed, Claire runs one `get_source_content` read herself.
   - Strip all personal names, cognitive typology labels, and workspace lore. State the factual constraints and proposal neutrally as an anonymous citizen's scenario.

3. **Clean-Room Registration & Parallel Dispatch:**
   - Register needed seats once per session via `define_subagent` (`EnableMCPTools: false`, `EnableWriteTools: false`, `EnableSubagentTools: false`).
   - Dispatch all selected seats in parallel via `invoke_subagent` (`Workspace: "inherit"`, `Model: "inherit"`, `Prompt: <Anonymized Case Brief>`).

4. **Weighted Synthesis & Cross-Examination:**
   - Weigh the 6 primary seats at `1.0x` and `council-entp` (mandatory Devil's Advocate dissent) at `0.5x` (elevate `council-entp` only if it catches a concrete flaw missed by the `1.0x` seats).
   - Never average into oatmeal: highlight the sharp splits, note any contrast between the impartial `council-intj` and Claire's dyadic `INTJ` view, and state Claire's ruling.
   - Retain each seat's `conversationId` and route follow-up challenges via `send_message`.
