---
name: iterate
description: >-
  Run one full adversarial Evaluate -> Plan -> Stage-Execute iteration cycle via
  /iterate without touching live workspace files or pushing to GitHub. Mirrors
  target files into an isolated staging sandbox, dispatches fresh clean-room
  workspace-aware critic subagents (7-seat Council in Claire, 3-seat domain panels
  in Grant, Chad, Andy, and Elmer) pointed at the staged tree as if previous rounds
  were already executed live, folds the changes into an environment-agnostic Master
  Plan artifact backed by staged file snapshots, and pauses for the next /iterate
  round or explicit environment submission.
---

# Adversarial Staged Iteration Protocol (`/iterate`)

Use this skill whenever the user invokes `/iterate` (or asks to run an iterative evaluate-plan-stage cycle on a repository, workspace, prompt, strategy, protocol, song chart, campaign prep, or draft).

## Hard Architectural Invariants (Non-Negotiable)

1. **Zero Default Live Mutation or Git Push:**
   - `/iterate` never modifies live workspace files, never mutates NotebookLM Vault sources, and never runs `git commit` or `git push` on live repositories by default.
   - All intermediate edits are executed strictly inside the conversation's isolated staging directory (`<artifact_dir>/scratch/iterate_stage/current/`).
2. **One Full Cycle Per Turn (`Evaluate -> Update Master Plan -> Apply in Staging -> Snapshot`):**
   - Each invocation of `/iterate` completes **one** full round and stops so the operator can inspect the round delta and either run `/iterate` (or say `"again"`) for the next round, steer the focus, or ask the workspace to submit the resulting plan to any named environment(s).
3. **Simulated Execution via Clean-Room Staging ("Make Them Think They Executed"):**
   - Never reuse prior subagent `conversationId`s across rounds.
   - On Round $N$, apply the newly planned changes directly to the files inside `<artifact_dir>/scratch/iterate_stage/current/`.
   - On Round $N+1$, spawn **fresh** clean-room critic subagents and point their prompt directly at `<artifact_dir>/scratch/iterate_stage/current/` presented as the current live build, with zero mention that prior rounds occurred or that the directory is a staging copy. This prevents evaluators from rubber-stamping their own prior edits and forces them to hunt for second-order flaws and remaining slop.

---

## Step-by-Step Round Execution

### Step 1: Initialize or Inspect the Isolated Staging Sandbox (`iterate_stage.py`)

Determine the conversation's staging path (`STAGE_DIR="<artifact_dir>/scratch/iterate_stage"`):

- **On Round 1** (when `manifest.json` does not yet exist in `STAGE_DIR` or when switching targets):
  Mirror the target file(s) or directory tree into `STAGE_DIR/current/`:
  ```bash
  python3 .agents/skills/iterate/scripts/iterate_stage.py init \
    --stage-dir "<artifact_dir>/scratch/iterate_stage" \
    --source "<target_path_1>" \
    --reset
  ```
- **On Round $N > 1$** (when continuing an active `/iterate` loop):
  Check the current round and staged state without resetting:
  ```bash
  python3 .agents/skills/iterate/scripts/iterate_stage.py status \
    --stage-dir "<artifact_dir>/scratch/iterate_stage"
  ```

### Step 2: Workspace-Aware Clean-Room Critic Dispatch

Select the critic panel based on the active workspace (unless the user explicitly overrides with `/iterate full-council` or a custom evaluation lens), reading [critic_panels.md](./references/critic_panels.md):

| Active Workspace | Default Critic Panel | Primary Seats (`1.0x`) | Devil's Advocate (`0.5x`) |
| :--- | :--- | :--- | :--- |
| **`claire-architect`** (or outside-of-project meta) | **7-Seat Veil-of-Ignorance Council** | `council-intj`, `council-intp`, `council-entj`, `council-enfj`, `council-istj`, `council-isfp` | `council-entp` |
| **`grant`** (Quantitative Research) | **3-Seat Quant Falsification Panel** | `quant-intp` (Overfitting/Stats), `quant-istj` (Slippage/Drawdown) | `quant-entp` (Regime/Crowding) |
| **`chad`** (Biometrics & Physiology) | **3-Seat Biometrics & Adherence Panel** | `bio-istp` (Endocrinology/Recency), `bio-istj` (Recovery/Adherence) | `bio-entp` (Confounding/Bloat) |
| **`andy`** (Studio & Songwriting) | **3-Seat Studio & Prosody Panel** | `studio-estp` (Pocket/Mouthfeel), `studio-isfp` (Anti-Cliche/Imagery) | `studio-entp` (Hook/Arrangement) |
| **`elmer`** (Tabletop & Campaign) | **3-Seat Tabletop Architecture Panel** | `dm-istj` (Canon/2024 5e RAW), `dm-entj` (Pacing/Player Agency) | `dm-entp` (PC Derailment) |

1. Register any needed critic seats via `define_subagent` (`EnableMCPTools: false`, `EnableWriteTools: false`, `EnableSubagentTools: false`).
2. Draft an **Anonymized Case Brief (`<150` Words)**:
   - Strip all personal names and workspace lore.
   - Reference the file paths inside `<artifact_dir>/scratch/iterate_stage/current/...` as the current build to inspect, plus `<artifact_dir>/scratch/iterate_stage/runtime_failure_traces.json` when non-empty so critics target recurring runtime hook blocks and tool errors alongside static code.
   - Never mention previous `/iterate` rounds or tell the subagents that changes were staged; let them inspect the current staged files cold as if that state is already executed and live.
   - **Zero Training Wheels (Outcome-Over-Implementation Framing):** State only the *operational goal* (what the system must achieve and its resource/token bounds), never earlier conversational assumptions about *how* to structure the files (unless the operator explicitly marked a constraint as non-negotiable). Explicitly authorize critics to delete redundant files, collapse dual-write ceremonies, and challenge the initial implementation shape from first principles.
3. Dispatch all selected seats in parallel via `invoke_subagent` (`Workspace: "inherit"`, `Model: "inherit"`, `Prompt: <Persona Header + Anonymized Case Brief>`).

### Step 3: Execute Round $N$ Fixes Inside the Staging Sandbox & Snapshot

Once all critic seats report back:

1. **Weigh & Synthesize:**
   - Weigh primary seats at `1.0x` and the Devil's Advocate dissenter (`*-entp`) at `0.5x` (elevating the Devil's Advocate to `1.0x` whenever it catches a concrete flaw or self-contradiction missed by the primary seats).
2. **Apply Changes to Staged Files (`<artifact_dir>/scratch/iterate_stage/current/`):**
   - Use `edit_file` or `write_file` to apply the round's fixes directly to the mirrored files inside `<artifact_dir>/scratch/iterate_stage/current/`.
   - If the staged tree contains a test suite or verification script (such as `unittest`, `audit_log.py epistemic --check`, or `<agent>_hooks.py SessionStart`), run it inside `<artifact_dir>/scratch/iterate_stage/current/` to verify all tests pass.
3. **Record the Round Snapshot & Run the Anti-Slop Gate:**
   ```bash
   python3 .agents/skills/iterate/scripts/iterate_stage.py snapshot-round \
     --stage-dir "<artifact_dir>/scratch/iterate_stage" \
     --summary "Round <N>: <concise summary of flaws fixed and staged>"
   ```
   If `snapshot-round` flags any `human-voice.md` anti-slop violation in the staged `.md` files, fix the offending staged file immediately and re-run `snapshot-round` until `slop_lint` returns `"PASS"`.

### Step 4: Update the Environment-Agnostic Master Plan Artifact (`iterate_plan.md`)

Create or update `<artifact_dir>/iterate_plan.md` (`UserFacing: true`, `RequestFeedback: false` during ongoing iteration rounds) using this exact portable structure so the operator can ask any workspace to submit the plan to any environment on demand:

1. **Cumulative Executive Summary & Current Round Verdict:**
   - State the current round number (`Round N`), the sharp splits from the critic panel, and what changed in this pass.
2. **Portable Logical Targets (Environment-Agnostic Action Plan):**
   - Group all cumulative modifications by logical target environment (for example: **Target A: Live Operating Workspace(s)**, **Target B: Public GitHub Showcase Repository**, **Target C: NotebookLM SSoT Vault**) so the operator can later say *"Submit Target A and Target B"* or *"Apply this plan to the showcase repo"*.
3. **Staged Snapshot Manifest & Round-by-Round Delta Log:**
   - Table linking each modified file in `<artifact_dir>/scratch/iterate_stage/current/<rel_path>` alongside its line count, token delta vs. baseline (`round_0_baseline`), and per-round changelog (`Round 1`, `Round 2`, ...).

### Step 5: Stop and Await Next Command

Present the concise Round $N$ synthesis and link to [`iterate_plan.md`](file://<artifact_dir>/iterate_plan.md) without re-summarizing the full artifact. Remind the operator they can:
- Run `/iterate` (or reply `"again"` / add a focus note) to launch **Round $N+1$** with fresh clean-room critics auditing the newly staged state as if it were already live, or
- Ask the workspace to submit [`iterate_plan.md`](file://<artifact_dir>/iterate_plan.md) to any target environment (using `iterate_stage.py apply --stage-dir ... --target <env_path>`).
