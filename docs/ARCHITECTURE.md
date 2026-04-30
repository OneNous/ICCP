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
