# Skills

Reusable know-how for BogiAgent, loaded with **progressive disclosure**: only
each skill's `name + description` goes into the system prompt; the full body is
pulled on demand via the `skill_read` tool. Loader: `bogi/modules/skills.py`
(V2 §2.F).

## Add a skill

Create `skills/<slug>/SKILL.md`:

```markdown
---
name: my-skill
description: One line the agent uses to decide if this skill is relevant.
---

The actual instructions / templates / examples (markdown body).
```

Keep `description` specific — it's the only thing the agent sees until it opens
the skill. Bodies can be long; they cost tokens only when read.

These are committed (unlike `vault/`, which is user content). No secrets.
