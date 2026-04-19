# Channel FSM state names (v2)

Persisted `readings.chN_state` values were renamed:

| Legacy        | New         |
|---------------|------------|
| `DRY`         | `OPEN`     |
| `WEAK_WET`    | `REGULATE` |
| `CONDUCTIVE`  | `REGULATE` |
| `PROTECTING`  | `PROTECTING` (unchanged) |
| `FAULT`       | `FAULT`    |

On logger startup, `DataLogger` runs an idempotent SQLite `UPDATE` on existing `readings` rows so dashboards and exports stay consistent.
