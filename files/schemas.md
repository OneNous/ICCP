# Schemas (Firmware Side)

> **Scope:** This file covers schema management within the firmware repo. The canonical source is in the monorepo (`coilshield/schemas/`). This file describes how firmware stays in sync.

## The Sync Problem

The firmware repo and the monorepo are separate. Schemas defined in the monorepo need to reach firmware code somehow. There are three options:

1. Git submodule (most rigorous, slight friction)
2. Manual sync (simplest, depends on discipline)
3. Generated package on a registry (heavyweight, overkill)

**Choice for validation phase: manual sync.** The owner copies the SQL files from the monorepo into firmware's `schemas/` directory whenever they change. After that, codegen runs locally.

Post-validation, evaluate moving to git submodule once schema changes become rare and stable.

## How Manual Sync Works

When the monorepo's schemas change:

```bash
# In the monorepo
$ pnpm run codegen:all  # Regenerates Swift, Dart, TypeScript

# Then manually:
$ cp coilshield/schemas/*.sql /path/to/coilshield-firmware/schemas/

# In the firmware repo
$ python codegen/gen_python.py  # Regenerates schema_types.py
$ git add schemas/ src/generated/schema_types.py
$ git commit -m "Sync schemas from monorepo v1.5.0"
```

The schema files include a version number in their header comment (see monorepo's `.claude/schemas-and-data.md` Rule SD-1). Firmware verifies the version on boot:

```python
# In src/main.py at startup
def verify_schema_version():
    with open('schemas/devices.sql', 'r') as f:
        first_lines = f.read(500)
    version = re.search(r'Version: (\S+)', first_lines).group(1)
    assert version == EXPECTED_SCHEMA_VERSION, f"Schema mismatch: {version} != {EXPECTED_SCHEMA_VERSION}"
```

If a deployed device has stale schemas, it crashes at boot. Better to fail loudly than to push malformed data to Supabase.

## Rule SC-1: Generated Files Are Read-Only

`src/generated/schema_types.py` is read-only. Don't edit by hand. The header includes:

```python
# =============================================================================
# AUTO-GENERATED FILE — DO NOT EDIT
# Source: schemas/*.sql v1.5.0
# Generated: 2026-04-29 by codegen/gen_python.py
# To make changes: edit SQL files in monorepo, sync, regenerate.
# =============================================================================
```

If you see this banner, edit the SQL, not the Python.

## Rule SC-2: Python Type Mapping

The codegen generator maps SQL types to Python types like this:

| SQL | Python |
|---|---|
| `UUID` | `str` (UUID stored as string for simplicity) |
| `TEXT` | `str` |
| `INTEGER` | `int` |
| `BIGINT` | `int` |
| `REAL` / `FLOAT` | `float` |
| `BOOLEAN` | `bool` |
| `TIMESTAMPTZ` | `datetime` (timezone-aware) |
| `JSONB` | `dict[str, Any]` |
| `TEXT[]` | `list[str]` |
| `TEXT NOT NULL` | required field |
| `TEXT` (nullable) | `Optional[str] = None` |

These get emitted as Python dataclasses:

```python
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

@dataclass
class Reading:
    id: str
    device_id: str
    channel: int
    timestamp: datetime
    current_ma: float
    polarization_mv: float
    temperature_c: float
    state: str
    fault_code: Optional[str] = None
```

## Rule SC-3: Use Generated Types in Code

When constructing rows for Supabase, use the generated dataclasses, not raw dicts:

```python
# Bad
row = {
    'device_id': self.device_id,
    'channel': 0,
    'curent_ma': 0.5,  # typo — won't be caught
    ...
}

# Good
from src.generated.schema_types import Reading

row = Reading(
    id=str(uuid.uuid4()),
    device_id=self.device_id,
    channel=0,
    current_ma=0.5,  # typo would be a syntax error
    timestamp=datetime.now(timezone.utc),
    ...
)
```

When pushing to Supabase, use `dataclasses.asdict()`:

```python
import dataclasses
supabase.table('readings').insert(dataclasses.asdict(row)).execute()
```

This way, every row written conforms to the schema. Schema changes break code at compile time (or at import time, in Python's case), not at runtime in Supabase.

## Rule SC-4: Enum Handling

SQL CHECK constraints become Python `StrEnum`:

```sql
state TEXT NOT NULL CHECK (state IN ('DORMANT', 'PROBING', 'PROTECTING', 'FAULT'))
```

becomes:

```python
from enum import StrEnum

class DeviceState(StrEnum):
    DORMANT = 'DORMANT'
    PROBING = 'PROBING'
    PROTECTING = 'PROTECTING'
    FAULT = 'FAULT'
```

Use the enum in code:

```python
# Bad
if self.state == 'protecting':  # case-sensitive comparison, easy to typo
    ...

# Good
if self.state == DeviceState.PROTECTING:
    ...
```

## Rule SC-5: Schema Version Is a Constant

The expected schema version lives in `config/settings.py`:

```python
EXPECTED_SCHEMA_VERSION = "1.5.0"
```

When schemas are synced, this constant gets bumped. Code that imports this constant can check it explicitly:

```python
from config.settings import EXPECTED_SCHEMA_VERSION
print(f"Running with schema {EXPECTED_SCHEMA_VERSION}")
```

This shows up in logs and in the `/info` endpoint response.

## Rule SC-6: Don't Add Firmware-Only Fields to Generated Types

If firmware needs to track something that isn't in the canonical schema, don't add it to the generated dataclass. Either:

1. Add it to the canonical schema in the monorepo (if it's part of the data model)
2. Use a separate firmware-only dataclass for internal state (in `src/internal_types.py`)

Don't blur the line between "what's in Supabase" and "what firmware tracks internally." The generated types are a contract; firmware-only state is implementation detail.

## Rule SC-7: When Schema Changes Are In Flight

The monorepo and firmware can be temporarily out of sync during development. To handle this:

1. Schema change made in monorepo, version bumped to 1.6.0-dev
2. Monorepo's apps and command center updated to v1.6.0-dev
3. Firmware NOT yet updated
4. Firmware continues to run against v1.5.0 schemas — Supabase still accepts them since v1.6.0-dev is additive
5. When firmware is ready, sync schemas, regenerate, test
6. Both sides bump to v1.6.0 (final)

For destructive schema changes (column removals, type changes), version bumps and firmware updates must happen simultaneously across all 10 deployed devices. This is rare during validation.

## Rule SC-8: Firmware-Specific Schema: Local SQLite

The firmware has its own local SQLite database for:

- `pending_uploads` (for cloud sync queue)
- `wet_sessions` (local cache of when each channel was wet)
- `commissioning_history` (local history, also synced to cloud)
- `bonded_devices` (BLE pairing keys — local-only, NEVER synced)

These local tables are NOT in the monorepo's canonical schemas. They're firmware-internal. Define them in `src/local_schema.py`:

```python
LOCAL_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS pending_uploads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_table TEXT NOT NULL,
    payload TEXT NOT NULL,
    queued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    retry_count INTEGER DEFAULT 0,
    last_error TEXT
);

CREATE TABLE IF NOT EXISTS wet_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel INTEGER NOT NULL,
    started_at TIMESTAMP NOT NULL,
    ended_at TIMESTAMP,
    average_current_ma REAL,
    peak_current_ma REAL
);

-- ... etc
"""
```

Run on startup if missing.

## Rule SC-9: Migration Paths for Local SQLite

The local SQLite database may need to be migrated as firmware evolves. Use a simple version table:

```python
def migrate_local_db(conn):
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS schema_meta (version INTEGER)")
    
    current = cursor.execute("SELECT version FROM schema_meta").fetchone()
    current_version = current[0] if current else 0
    
    if current_version < 1:
        cursor.execute("...")  # v1 migrations
    if current_version < 2:
        cursor.execute("...")  # v2 migrations
    
    cursor.execute("UPDATE schema_meta SET version = ?", (LATEST_LOCAL_SCHEMA_VERSION,))
    conn.commit()
```

Don't drop and recreate. Don't lose pending_uploads on migration. Local data is recoverable; cloud-side data isn't.

## Common Cursor Pitfalls in Schema Code

- Suggesting `pydantic` for type validation (overkill; dataclasses + manual validation is fine)
- Adding fields to generated types instead of editing SQL
- Using string literals instead of generated enums
- Forgetting that `asdict()` is recursive (nested dataclasses get converted too)
- Suggesting an ORM (SQLAlchemy, etc.) — we use raw SQL via the Supabase REST API; no ORM needed

## Smoke Test for Schema Sync

Before declaring schemas "validation-ready":

1. Schema files in `schemas/` match version in monorepo
2. `EXPECTED_SCHEMA_VERSION` constant matches schema files
3. Generated types in `src/generated/schema_types.py` match the SQL
4. Firmware imports without errors
5. A row constructed from the generated dataclass posts successfully to Supabase
6. Schema version mismatch correctly causes startup to fail
7. Local SQLite tables created on fresh install
8. Local schema migrations run cleanly on existing install with older local schema

If any step fails, schemas are not validation-ready.
