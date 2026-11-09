---
trigger: always_on
description: Hydrates the three core <100-line workspace state files inline at Turn 0 with zero tool-call overhead.
---

# INLINE STATE HYDRATION (Turn-Zero State Expansion)

The three core workspace state files below are expanded inline into `<user_rules>` on every turn. Do NOT call `read_file` on them at turn start unless mutated mid-turn.

@[state/ecosystem_registry.md](state/ecosystem_registry.md)

@[state/routing_matrix.md](state/routing_matrix.md)

@[state/active_sprint.md](state/active_sprint.md)
