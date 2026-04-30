# Firmware architecture (hub)

This file is the **canonical entrypoint** named in [`claude.md`](../claude.md). For depth, use the linked docs below.

## Runtime and control

- Supervisor / main loop and process model: [supervisor-architecture.md](supervisor-architecture.md)
- Channel I2C, reference path, mux: [architecture-channel-i2c-reference.md](architecture-channel-i2c-reference.md)
- CLI surface and subcommands: [iccp-cli-reference.md](iccp-cli-reference.md)
- Pi edge (BLE Wi‑Fi, MQTT, register): [pi-edge-deploy.md](pi-edge-deploy.md)
- IoT dual-system agent model (optional uplink): [iot-dual-system/agent-process-model.md](iot-dual-system/agent-process-model.md)

## Code layout

Python modules and `pi_edge/` live under [`src/`](../src/) per `claude.md` and `pyproject.toml` (`package-dir` maps top-level modules there). `config/` stays at the repository root next to `src/`. Shared assets: `static/` (dashboard fonts), `tui.tcss` (next to `src/tui.py`).

## Generated types

[`src/generated/schema_types.py`](../src/generated/schema_types.py) is a placeholder until monorepo codegen is wired (see [.claude/schemas.md](../.claude/schemas.md) and [`codegen/README.md`](../codegen/README.md)).

## Logging: stdout vs durable telemetry

- **Durable path:** `logger.DataLogger.record()` → SQLite (`coilshield.db` by default), atomic `latest.json`, CSV, fault log. Anything required for history, dashboards, or cloud enqueue belongs here or in explicit log files.
- **Supervisor / JSONL:** When `ICCP_OUTPUT_MODE=jsonl`, `iccp_runtime` should emit structured lines via `cli_events.emit()` (thermal pause/resume, start metadata) instead of ad-hoc `print()` so log aggregators get parseable events without scraping human tables.
- **Human UX:** `print()` / Rich output in `iccp_cli`, `dashboard` (non-API), `tui`, and commissioning prompts stay on stdout; they are not a substitute for `logger` sinks.
- **BLE / edge:** `pi_edge/` may use stderr for verbose traces; gate with env flags where possible.

See also [.claude/cross-cutting.md](../.claude/cross-cutting.md) (logging table).
