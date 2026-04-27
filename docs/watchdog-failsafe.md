# Watchdog, GPIO failsafe, and SQLite flush

## Hardware watchdog (Raspberry Pi)

The application process can hang (I2C stall, deadlock) while GPIO may still energize anodes. Use the **SoC watchdog** so a stuck userspace process eventually triggers a reboot.

1. In `/boot/firmware/config.txt` (or `/boot/config.txt` on older images), add:

   ```
   dtparam=watchdog=on
   ```

2. Let **systemd** feed `/dev/watchdog` from the `iccp` service, e.g. a unit that calls a small helper to write to the device, or use `systemd`’s built-in watchdog support with a **WatchdogSec=** on a **Type=notify** service (see [systemd.service(5)](https://www.freedesktop.org/software/systemd/man/systemd.service.html)).

3. **SIGKILL** and kernel panics are not recoverable in Python; the hardware watchdog is the mitigation for “process completely gone.”

## Python exit path (best-effort)

- **SIGINT / SIGTERM:** [`iccp_runtime.run_iccp_forever`](../iccp_runtime.py) calls `Controller.all_outputs_off()` and [`DataLogger.flush()`](../logger.py) before exit.
- **Normal shutdown:** `atexit` runs the same **GPIO off + flush** path so buffered SQLite `readings` and CSV are less likely to be lost.
- **Hard crash (SIGSEGV, OOM killer, power loss):** not fixable in Python; **watchdog** + **hardware** failsafe if you add it.

## Commissioning crash recovery

See `commissioning_complete` in `commissioning.json`: a full successful `iccp commission` sets this to `true`. If the file is partial (no flag or `false`), [`needs_commissioning()`](../commissioning.py) treats the install as incomplete so the next boot re-runs commissioning. Legacy files without the key but with `native_mv` and `commissioned_target_ma` are accepted with a one-time stderr notice.
