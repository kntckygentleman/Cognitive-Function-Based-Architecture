---
name: agent-scaffolder
description: >-
  Use this skill when the user approves building a new subagent or skill
  in the claire-architect workspace. Generates a compliant .agents/agents/<name>.md
  pre-wired with human-voice rules, NotebookLM Vault UUID bindings, and Kortex MCP
  token-conservation constraints.
---

# Workspace Agent & Skill Scaffolder

Use this skill to stamp out new subagents (`.agents/agents/<name>.md`) without hand-writing boilerplate or omitting Kortex token-conservation rules.

## Pre-Scaffold Check
1. Confirm the build is approved in `state/active_sprint.md`.
2. Identify which NotebookLM Vault notebook(s) in `state/ecosystem_registry.md` the agent or skill will read or mutate, including their exact `notebook_id` and canonical `00_STATE_` / `01_STATE_` `source_id` UUIDs.

## Steps

1. Run the generator script [scaffold_agent.py](./scripts/scaffold_agent.py) with the agent name, description, and target notebook key(s):
   ```bash
   python3 .agents/skills/agent-scaffolder/scripts/scaffold_agent.py \
     --name "media-storyboard-director" \
     --description "Media storyboard bible and video prompt subagent." \
     --notebooks "media"
   ```
2. Inspect the generated file in `.agents/agents/<name>.md` and fill in any domain-specific operational rules under `## Domain Operational Protocol`.
3. Update `state/ecosystem_registry.md` and `state/active_sprint.md` once the agent is verified.
