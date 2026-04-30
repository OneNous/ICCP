# Codegen (monorepo → firmware)

Per [`claude.md`](claude.md) **rule 2** and [`.claude/schemas.md`](.claude/schemas.md):

1. Author or change JSON schemas in the **`coilshield`** monorepo (`coilshield/schemas/`).
2. Copy the canonical files into this repo’s [`schemas/`](../schemas/).
3. Run the monorepo’s **codegen** pipeline here (this directory is the conventional home for scripts) to refresh [`src/generated/schema_types.py`](../src/generated/schema_types.py).

Until that pipeline exists, `schema_types.py` remains a **placeholder**—do not hand-maintain large generated bodies in firmware.
