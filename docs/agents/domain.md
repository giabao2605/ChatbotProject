# Domain docs

## Before exploring

- Read root `CONTEXT.md` when it exists.
- Read root `CONTEXT-MAP.md` when it exists, then the relevant context documents it links to.
- Read ADRs in `docs/adr/` that affect the area being changed.

If these files do not exist, proceed without raising their absence. Domain-modeling work creates them only when terminology or decisions need to be recorded.

## Layout

This repository uses the single-context layout:

```text
/
├── CONTEXT.md
├── docs/
│   └── adr/
└── src/
```

## Consumer rules

- Use terms defined in `CONTEXT.md`; avoid introducing conflicting synonyms.
- If a needed term is absent, record the gap for domain-modeling work.
- Surface any contradiction with an ADR explicitly instead of silently overriding it.
