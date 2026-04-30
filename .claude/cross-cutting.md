# Cross-Cutting Concerns (Firmware)

> **Scope:** Read this when work spans multiple firmware sub-systems or when you're not sure which sub-rule file applies. The catch-all.

## Logging Architecture

The firmware has multiple logging surfaces. Know which one to use for what:

| Log type | Where | When to use |
|---|---|---|
| Structured event | `logger.py` → SQLite + JSON + CSV | State transitions, faults, wet events, commissioning steps |
| Critical fault | `fault.log` | Anything that requires operator attention |
| BLE diagnostic | `bled.log` | BLE pairing, advertising, characteristic operations |
| Tech API access | `tech_api.log` | Every authenticated HTTP request |
| systemd journal | `journalctl -u coilshield` | Process startup, shutdown, exceptions |
| JSONL supervisor | `cli_events.emit()` when `ICCP_OUTPUT_MODE=jsonl` | Machine-readable lines from `iccp_runtime` (thermal pause/resume, start banner data, etc.) |
| Human CLI / TUI / dashboard | `stdout` / Rich | Interactive UX only — not the durability path for controller telemetry |

**Policy (validation phase):** Do **not** add `print()` on the hot control path for “logs” that operators must retain. Controller truth remains `logger.py` sinks (`latest.json`, SQLite, CSV). For automation-friendly process logs, use `cli_events.emit()` when `output_mode() == "jsonl"` (see `iccp_runtime` thermal events). `print()` remains acceptable for interactive `iccp` / TUI / dashboard and for **stderr**-gated BLE debug in `pi_edge/` (reduce churn there until BLE work is active).

The older rule “never use bare print” applies to **production durability** and **steady-state controller noise** — not to removing all human-facing stdout from the CLI.

## State Persistence

The firmware survives reboots. State that must persist across reboots:

- WiFi credentials (in `wpa_supplicant.conf`, managed by OS)
- BLE bonding keys (in `/var/lib/coilshield/bonded_devices.json` and `/var/lib/bluetooth/`)
- Device identity (in `/etc/coilshield/env`)
- Pending cloud uploads (in `/var/lib/coilshield/local.sqlite`)
- Wet session history (in `/var/lib/coilshield/local.sqlite`)
- Latch fault state (in `/var/lib/coilshield/fault_state.json`)

State that can be lost on reboot:

- Current per-channel state machine state (rebuilds from sensors on boot)
- Last-known polarization reading (rebuilds from sensors on boot)
- In-progress commissioning sequence (canceled on reboot, must restart)
- BLE advertising state (re-evaluated on boot based on WiFi config)

Don't try to persist things in the second list. They're better recomputed than restored.

## Concurrency Model

The firmware is **mostly single-threaded** with two exceptions:

1. **Cloud sync thread** — runs in background, drains queue to Supabase
2. **Flask server thread(s)** — for the dashboard and tech API

The control loop is the main thread. It must not block on cloud sync, network calls, or HTTP requests.

Inter-thread communication uses queues:

```python
import queue
upload_queue = queue.Queue(maxsize=10000)

# Control loop (main thread)
upload_queue.put_nowait(reading)  # Non-blocking; drops if full

# Cloud sync thread
while True:
    batch = []
    while len(batch) < 50:
        try:
            batch.append(upload_queue.get(timeout=1))
        except queue.Empty:
            break
    if batch:
        push_to_supabase(batch)
```

Don't use shared mutable state between threads. Don't use threading locks unless absolutely required (and document why).

## Time Handling

Always use UTC for timestamps:

```python
from datetime import datetime, timezone
now = datetime.now(timezone.utc)
```

Never use `datetime.now()` without timezone — naive datetimes lead to bugs that only manifest at DST transitions.

When formatting for display (logs, dashboard), convert to local time at the display layer. Storage and computation are always UTC.

## Error Handling Patterns

### Pattern 1: Sensor Read

```python
def read_current(channel):
    try:
        return ina3221.current(channel)
    except (OSError, IOError) as e:
        self.logger.warn(f"INA3221 ch{channel} read failed: {e}")
        return None  # Caller decides what to do with None
```

### Pattern 2: Network Call

```python
def push_reading(reading):
    try:
        return supabase.table('readings').insert(asdict(reading)).execute()
    except Exception as e:
        self.logger.error(f"Cloud push failed: {e}")
        local_db.queue_pending(reading)  # Don't lose data
        return None
```

### Pattern 3: State Transition

```python
def transition_to_fault(self, reason):
    self.logger.fault(f"Channel {self.channel} → FAULT: {reason}")
    self.state = ChannelState.FAULT
    self.gate_off()
    self.persist_fault_state()
```

State transitions are always logged. Always.

### Pattern 4: Catastrophic Failure

```python
def main():
    try:
        run_control_loop()
    except KeyboardInterrupt:
        graceful_shutdown()
    except Exception as e:
        # Log everything, then exit non-zero so systemd restarts us
        self.logger.critical(f"Fatal error: {e}", exc_info=True)
        gate_all_channels_off()
        sys.exit(1)
```

If the control loop dies, gate all channels off before exiting. systemd will restart the process; we want all channels off during the restart gap.

## Testing Strategy

Layered testing, in order of risk:

### Unit Tests (`tests/`)

Pure logic, no hardware. Use the simulator:

```python
# tests/test_control.py
from src.control import Channel, ChannelState
from src.sensors import SensorSimulator

def test_dormant_to_protecting_on_wet():
    sensors = SensorSimulator(profile='clean')
    channel = Channel(0, sensors)
    sensors.set_wet(0)
    
    channel.tick()
    assert channel.state == ChannelState.PROBING
    
    sensors.advance_time(60)
    channel.tick()
    assert channel.state == ChannelState.PROTECTING
```

Run with `pytest`. CI runs these on every commit.

### Bench Tests

Real Pi, real hardware, simulated electrolyte. Steel pliers as cathode (safe — steel tolerates overprotection). 

Catches:
- I2C bugs
- GPIO timing issues
- Real sensor noise patterns
- Long-running stability issues

### Coupon Tests

Real Pi, real hardware, aluminum fin sample as cathode. **Required before any real-coil deployment.**

Catches:
- Aluminum-specific overprotection sensitivity
- Calibration of the safety cutoff in real conditions
- Long-term stability with realistic electrolyte chemistry

### Real Coil Tests

Only after coupon tests pass cleanly. The 10 validation units.

## Resource Constraints

Pi 3 specs to remember:

- 1 GB RAM (BCM2835 has 1GB; some Pi 3B+ have 1GB; Pi 4 has 2-8GB)
- 4 cores @ 1.4 GHz
- WiFi 802.11ac (2.4 GHz band — 5 GHz also supported but most home routers are 2.4 for compatibility)
- Bluetooth 4.2

These are not powerful constraints for our workload, but:

- Don't load entire SQLite databases into memory
- Don't keep references to old readings (let them be GC'd)
- Profile periodically to catch memory leaks
- Cloud sync queue capped at 30 days specifically because RAM + SD card are finite

## Dependencies Discipline

`requirements.txt` is the dependency list. Every entry is justified.

Acceptable dependencies:

- `RPi.GPIO`, `gpiozero` — GPIO/PWM
- `smbus2` — I2C
- `adafruit-circuitpython-ads1x15` — ADS1115 driver
- `bless`, `bleak` — BLE
- `flask`, `flask-limiter` — web server
- `requests` — HTTP client (cloud sync)
- `sdnotify` — systemd watchdog
- `python-multipart` — form uploads (if needed)

Avoid:

- ORMs (SQLAlchemy, etc. — raw SQL via Supabase REST is enough)
- Async frameworks layered on Flask (FastAPI, Quart — Flask sync is fine)
- Heavy validation libraries (pydantic — dataclasses + manual validation is enough)
- "Convenience" libraries that wrap simple stdlib calls (use stdlib)

When you need a new dependency:
1. Search if stdlib covers it
2. Check if existing deps cover it
3. Add only if neither — and justify in a commit message

## When You Genuinely Need to Refactor

Some refactoring will eventually be needed. Rules:

- **During validation: don't.** Working code stays. Don't "improve" things that aren't broken.
- **Post-validation: discuss first.** A refactor is an architectural change. Document the motivation, alternatives considered, and proposed approach in `docs/DECISIONS.md` BEFORE writing code.
- **Bug-driven refactor is okay.** If a bug requires restructuring code to fix it properly, refactor as part of the bug fix. Document in the commit message.
- **Test coverage before refactor.** Don't refactor untested code without first adding tests. Otherwise you can't verify the refactor preserved behavior.

## Common Cursor Pitfalls Specific to This Codebase

Listed across all sub-files but worth consolidating:

- Suggesting `asyncio` for the control loop
- Replacing Flask with FastAPI
- Adding type hints inconsistently (commit to one style)
- Using `print()` for logging
- Catching exceptions broadly without logging
- Suggesting third-party libraries when stdlib works
- Refactoring "for cleanliness" without changing behavior
- Forgetting that the Pi is a hardware-constrained device
- Trying to make the firmware "platform-independent" — it's Pi-specific by design

## When You Need Context From Outside the Firmware Repo

The firmware repo is standalone but the system isn't. If a task requires understanding:

- How the apps interact with firmware → ask owner to paste relevant monorepo `.claude/` content
- The end-to-end data flow → ask owner to paste `docs/ARCHITECTURE.md` from monorepo
- Schema changes that originated in the monorepo → ask owner for the relevant `schemas/*.sql` and the DECISIONS entry that explains the change

Don't guess. Ask.
