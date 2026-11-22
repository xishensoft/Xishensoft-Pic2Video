# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

**Layout: single-context.** One repository, one shared vocabulary: a root `GLOSSARY.md` plus `docs/adr/` for architecture decisions. There is no `GLOSSARY-MAP.md` and no per-subproject glossary, so don't create or look for them until a real term or decision has been written down.

## Before exploring, read these

- **`GLOSSARY.md`** at the repo root, or
- **`GLOSSARY-MAP.md`** at the repo root if one ever exists (it does not today): it would point at one `GLOSSARY.md` per context.
- **`docs/adr/`**: read ADRs that touch the area you're about to work in.

If any of these files don't exist, **proceed silently**. Don't flag their absence; don't suggest creating them upfront. The `/domain-modeling` skill (reached via `/grill-with-docs` and `/improve-codebase-architecture`) creates them lazily when terms or decisions actually get resolved.

## File structure

Single-context repo (this one):

```
/
├── GLOSSARY.md          ← created lazily, first real term only
├── docs/
│   ├── adr/             ← architecture decisions
│   ├── agents/          ← this configuration (issue tracker, triage labels, domain docs)
│   └── issues/          ← legacy Markdown tickets, read-only history
└── (flat Python modules at the root: pic2video.py, components.py, audio_source.py, …)
```

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a test name), use the term as defined in `GLOSSARY.md`. Don't drift to synonyms the glossary explicitly avoids.

If the concept you need isn't in the glossary yet, that's a signal: either you're inventing language the project doesn't use (reconsider) or there's a real gap (note it for `/domain-modeling`).

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly rather than silently overriding:

> _Contradicts ADR-0007 (event-sourced orders), but worth reopening because…_
