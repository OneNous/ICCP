# Codegen (monorepo → firmware)

Per [`claude.md`](claude.md) **rule 2** and [`.claude/schemas.md`](.claude/schemas.md):

1. Author or change JSON schemas in the **`coilshield`** monorepo (`coilshield/schemas/`).
2. Copy the canonical files into this repo’s [`schemas/`](../schemas/).
3. Run codegen to refresh [`src/generated/schema_types.py`](../src/generated/schema_types.py):

```bash
python3 codegen/gen_types.py
```

4. CI drift check (no write):

```bash
python3 codegen/gen_types.py --check
```

[`gen_types.py`](gen_types.py) emits a **small manifest** (SHA-256 per schema + loader helper) until the monorepo publishes full `datamodel-codegen` output. Do not hand-edit `schema_types.py` except by regenerating.
