#!/usr/bin/env python3
"""Scaffolds compliant domain subagents in .agents/agents/<name>.md.

Pre-wires:
- @ include for .agents/rules/human-voice.md
- Target NotebookLM Vault UUIDs and canonical 00_STATE_ / 01_STATE_ source_ids
- Kortex MCP token-conservation protocol (citation_format="none", bulk for >3 calls)
"""

from __future__ import annotations

import argparse
import os
import sys

WORKSPACE_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

VAULT_REGISTRY = {
    "architecture": {
        "title": "Orchestrator Architecture Vault",
        "notebook_id": "00000000-0000-4000-8000-000000000100",
        "state_sources": [
            ("01_Operator_And_Counterweight_Spec.md", "00000000-0000-4000-8000-000000000101"),
            ("02_Active_Operations_And_Sprint_Protocols.md", "00000000-0000-4000-8000-000000000102"),
            ("03_Shared_Calendar_And_Comms_Governance.md", "00000000-0000-4000-8000-000000000103"),
            ("04_Chronological_Changelog_Directives.md", "00000000-0000-4000-8000-000000000104"),
        ],
    },
    "media": {
        "title": "Media Storyboard Vault",
        "notebook_id": "00000000-0000-4000-8000-000000000200",
        "state_sources": [
            ("00_STATE_Media_Production_Spec.md", "00000000-0000-4000-8000-000000000201"),
        ],
    },
}


def build_agent_markdown(
    name: str, description: str, notebook_keys: list[str], include_state: bool
) -> str:
    lines = [
        "---",
        f"name: {name}",
        "description: >-",
        f"  {description}",
        "---",
        "",
        "@[.agents/rules/human-voice.md](.agents/rules/human-voice.md)",
        "",
    ]
    if include_state:
        lines.extend([
            "@[.agents/rules/state-hydration-example.md](.agents/rules/state-hydration-example.md)",
            "",
        ])

    lines.extend([
        f"# SUBAGENT IDENTITY: {name.upper()}",
        "",
        "## Bound NotebookLM Vault Sources",
    ])

    for key in notebook_keys:
        entry = VAULT_REGISTRY.get(key)
        if not entry:
            continue
        lines.append(f"- **{entry['title']}** (`notebook_id`: `{entry['notebook_id']}`):")
        for src_name, src_id in entry["state_sources"]:
            lines.append(f"  - `{src_name}` (`source_id`: `{src_id}`)")

    lines.extend([
        "",
        "## Kortex MCP Execution Invariants",
        "1. Always pass the exact `notebook_id` / `source_id` UUIDs above. Never call `list_notebooks` or `list_sources` unless auditing for newly uploaded files.",
        "2. Read canonical `00_STATE_` / `01_STATE_` Markdown sources first via `get_source_content` (~2-5 KB) before querying archival sources.",
        "3. When calling `ask_question`, always pass `citation_format: \"none\"` (or `\"inline\"`) and reuse `session_id` across follow-up queries.",
        "4. Wrap any sequence of >3 Kortex tool calls in a single `bulk` call (`action: \"start\"`).",
        "",
        "## Domain Operational Protocol",
        "- Execute the assigned domain task concisely and return verified findings or staged state updates to the lead architect.",
        "",
    ])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Scaffold a compliant subagent in .agents/agents/")
    parser.add_argument("--name", required=True, help="Lowercase hyphenated subagent name")
    parser.add_argument("--description", required=True, help="Subagent description for frontmatter")
    parser.add_argument(
        "--notebooks",
        default="",
        help=f"Comma-separated keys from: {', '.join(VAULT_REGISTRY.keys())}",
    )
    parser.add_argument("--hydrate-state", action="store_true", help="Include state-hydration rule")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite if file exists")
    args = parser.parse_args()

    nb_keys = [k.strip() for k in args.notebooks.split(",") if k.strip()]
    out_dir = os.path.join(WORKSPACE_ROOT, ".agents/agents")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{args.name}.md")

    if os.path.exists(out_path) and not args.overwrite:
        sys.stderr.write(f"Error: {out_path} already exists. Pass --overwrite to replace.\n")
        sys.exit(1)

    content = build_agent_markdown(args.name, args.description, nb_keys, args.hydrate_state)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"Created {out_path}")


if __name__ == "__main__":
    main()
