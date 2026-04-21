# Stale dashboard live feed and fault notice

When the dashboard shows **Live feed is stale** or **Fault latch is active**, use this checklist on the **same machine** that runs the controller and (usually) the dashboard.

## What “stale” means

The UI compares wall clock to **`latest.json` modification time** (`feed_age_s`). If the file has not been rewritten for a long time, either:

1. The **controller** (`main.py` or `iccp -start`) is **not running** or crashed, or  
2. The dashboard is reading a **different directory** than the controller writes (`LOG_DIR` / `COILSHIELD_LOG_DIR` mismatch).

**Fault latch** in the banner reflects the **last** snapshot inside `latest.json`. If the feed is stale, that latch can be **old** from the last tick before the controller stopped.

## Verification (run on the Pi)

| Check | Command / action |
|--------|-------------------|
| Controller running? | `systemctl status iccp` (adjust unit name if yours differs), or `ps aux \| grep -E 'main.py\|iccp'` |
| `latest.json` age vs clock | `stat /path/to/logs/latest.json` — compare **Modify** time to `date` |
| Controller `LOG_DIR` | With the **same** venv/cwd as systemd: `python3 -c "import config.settings as c; print(c.LOG_DIR)"` — run once **without** `COILSHIELD_LOG_DIR`, then with the same `Environment=` as your `iccp` unit |
| Dashboard `LOG_DIR` | Same one-liner in the shell **where you start** `dashboard.py`, or ensure systemd `Environment=COILSHIELD_LOG_DIR=...` matches **exactly** |
| Clear fault (after you fix the root cause) | `iccp clear-fault` or touch `clear_fault` under the project root (see `config.settings.CLEAR_FAULT_FILE`) |

If `stat` shows an old mtime and `systemctl` shows **inactive** or **failed**, inspect **`journalctl -u iccp -e`**, fix the unit, then `sudo systemctl enable --now iccp`. The dashboard cannot refresh data if nothing writes `latest.json`.

## Fix runtime alignment

1. Pick **one** absolute telemetry directory (example: `/home/onenous/coilshield/logs`).
2. Set the **same** value everywhere:
   - **systemd:** uncomment or add `Environment=COILSHIELD_LOG_DIR=/abs/path/logs` in both [`deploy/iccp.service`](../deploy/iccp.service) and [`deploy/dashboard.service`](../deploy/dashboard.service) (copy examples into `/etc/systemd/system/…`, then `daemon-reload` and restart both units).
   - **Manual:** `export COILSHIELD_LOG_DIR=/abs/path/logs` before both processes, **or** pass **`--log-dir /abs/path/logs`** to `iccp -start`, `python3 main.py`, and `python3 dashboard.py` (see [`config/argv_log_dir.py`](../config/argv_log_dir.py)).
3. Restart the controller, then confirm **`stat`** updates every tick (~`SAMPLE_INTERVAL_S`).
4. Clear faults only after the loop is healthy: `iccp clear-fault`.

## Related README / API

- README “Live data” and “Dashboard vs hardware” — same `COILSHIELD_LOG_DIR` / `ICCP_LOG_DIR` / `--log-dir`.
- `GET /api/live` includes **`telemetry_paths`** and **`feed_age_s`** / **`feed_stale_threshold_s`** for debugging.
