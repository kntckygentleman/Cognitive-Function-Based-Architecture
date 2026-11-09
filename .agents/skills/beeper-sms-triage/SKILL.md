---
name: beeper-sms-triage
description: >-
  Triage SMS and RCS threads via Beeper Desktop API (localhost:23373) using an
  isolated worker subagent to keep the parent context window clean, sample
  50 per-thread outgoing messages for register matching, extract calendar events,
  and gate outbound replies behind a two-phase human confirmation workflow.
---

@[.agents/rules/human-voice.md](.agents/rules/human-voice.md)

# Beeper Desktop SMS & RCS Triage Skill

Use this skill to triage, search, or reply to SMS/RCS threads via Beeper Desktop (`http://127.0.0.1:23373`) while keeping raw message payloads out of the primary orchestrator context.

## Hard Architectural Invariants

1. **Mandatory Subagent Delegation for Triage & Search (Zero Main-Thread Bloat):**
   - Never run `beeper_triage.py triage` or `beeper_triage.py search` directly inside the primary conversation. Raw message JSON dumps belong in a disposable worker subagent context.
   - Define `sms-triage-worker` once per session via `define_subagent` (`EnableWriteTools: true`, `EnableMCPTools: false`, `EnableSubagentTools: false`) and dispatch it via `invoke_subagent` (`Model: "inherit"`, `Workspace: "inherit"`).
   - Instruct the worker to run `beeper_triage.py triage` (or `search`), absorb the raw JSON in its own context window, and reply to the lead agent in `<=175` words containing only:
     1. Actionable or unread human threads (`chat_id`, contact/group title, timestamp, and exact question/message),
     2. Extracted calendar events or scheduling questions (dates, times, participants),
     3. Recommended follow-up reminders or candidate replies to stage.
2. **Deterministic Outbound Gate (`PreToolUse` Hook + Two-Phase CLI):**
   - Direct `call_mcp_tool(ServerName="beeper", ToolName="send_message")` calls are blocked at the hook layer by [`.agents/scripts/claire_hooks.py`](../../scripts/claire_hooks.py).
   - **Step 0 (50-Message Per-Thread Register Sample):** Before drafting any reply, delegate to `sms-triage-worker` to run `beeper_triage.py voice-sample --chat-id "<ID>" --count 50`. The worker reads the operator's last 50 outgoing messages in that specific thread (`my_last_messages`) to match exact casing, punctuation, length, and tone with that specific contact.
   - **Step 1 (Stage):** Run `beeper_triage.py stage-reply --chat-id "<ID>" --text "<DRAFT>"` to write the draft to `state/epistemic_manifest.json` (`staged_replies`) and generate an `approval_id` (`RPL-xxxxxx`). Display the target contact, thread title, and exact text to the operator.
   - **Step 2 (Transmit):** Only after explicit operator approval in a subsequent turn, run `beeper_triage.py send-staged --approval-id "RPL-xxxxxx"`.
3. **Calendar Permission Boundary (`gcalendar`):**
   - `Primary` (`<OPERATOR_PRIMARY_CALENDAR>`) and `Shared` (`<SHARED_TEAM_CALENDAR>`) are writable hard-block calendars (`accessRole: owner`).
   - Secondary calendars (`Work`, `Appointments`, `Events`) are strictly read-only (`accessRole: reader`). Items on `Events` are treated as tentative schedule radar unless promoted onto `Shared` or `Primary`.
