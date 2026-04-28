# Desktop / Command Center integration (ICCP side)

External apps (Electron, Tauri, mobile wrappers) should treat the **Flask dashboard** as the
read-only telemetry API. **Mutations** (start/stop, commission, clear-fault) stay on the `iccp`
CLI or `systemctl` over SSH — the HTTP server intentionally does not expose write/control routes.

## Run the API on the Pi

1. Controller writes telemetry: `iccp start` (or `systemctl start iccp`).
2. Dashboard serves JSON: `iccp dashboard` (default **http://0.0.0.0:8080**).

Match **`COILSHIELD_LOG_DIR` / `ICCP_LOG_DIR`** (or `iccp dashboard --log-dir`) with the controller
so `latest.json` and SQLite paths align.

### SSH tunnel (typical for a desktop app)

Forward the Pi’s dashboard to your machine:

```bash
ssh -L 9080:127.0.0.1:8080 pi@<pi-host>
```

Then the app uses base URL **`http://127.0.0.1:9080`**.

If the dashboard is bound only on the Pi loopback (`iccp dashboard --host 127.0.0.1`), it is
reachable only via SSH forward or on the Pi itself — that is a reasonable hardening choice when
the LAN should not hit the API.

## Remote monitoring (away from home)

Command Center–style apps keep using **the same sequence**: SSH to the Pi, start a **local** TCP
forward to `127.0.0.1:<dashboard-port>` on the Pi, then `fetch('http://127.0.0.1:<local-port>/api/…')`
on the laptop/phone. Nothing in that path requires you to be on the home LAN — it only requires
**SSH to reach the Pi** from wherever you are.

**At home (LAN):** use the Pi’s private address (e.g. `pi@192.168.1.50`) in your SSH profile.

**Away from home:** point the SSH **host** at something routable on the internet without exposing
the HTTP dashboard directly:

1. **Mesh / overlay VPN (recommended)** — Install **Tailscale**, **ZeroTier**, **Headscale**, or
   similar on the Pi and on the machine running the desktop app. Use the Pi’s VPN hostname or IP
   (e.g. `pi@raspberrypi.tail1234.ts.net`) as the SSH host. You keep the same tunnel + `fetch`
   pattern; traffic is encrypted by the mesh and you avoid opening the dashboard port on your
   router. This also works on the LAN (same hostname often resolves), so one profile is enough, or
   you can save two profiles (“LAN” vs “Tailscale”) if you prefer lower latency at home.

2. **SSH port forward on the router** — Forward a **WAN** TCP port to **Pi:22** only, disable
   password auth, use keys, and consider a non-default SSH port and lockdown (fail2ban, allowlists).
   The dashboard **`/api/*` surface has no login**; treat **network reachability of port 8080** as
   full read access to telemetry. **Do not** port-forward **8080** to the public internet unless
   you put a separate authenticated reverse proxy in front (out of scope for default ICCP).

3. **Bastion / jump host** — SSH to a small VPS or home gateway, then `ProxyJump` / `-J` to the Pi;
   the desktop app must support whatever your client uses (multi-hop SSH is often configured in
   `~/.ssh/config` so the app still sees a single `Host`).

**Security reminder:** CORS allows browser/Electron `fetch` from arbitrary *origins*, but your data
still only flows over **SSH + localhost** if you use a local forward. The weak spot is **who can
open TCP to whatever you use for SSH** (or join your VPN), not CORS itself.

## CORS

All **`/api/*`** responses include **`Access-Control-Allow-Origin: *`** and answer **`OPTIONS`**
preflight so a renderer on another origin (e.g. Vite `http://localhost:5173`) can `fetch` the
tunneled `http://127.0.0.1:<local-port>/api/...` URL. **There is no cookie/session auth** on these
routes; network access to the bind address is the security boundary.

## Endpoints (GET, JSON)

| Path | Purpose |
|------|---------|
| **`/api/meta`** | Package name/version, `num_channels`, `target_ma`, `max_ma`, `sample_interval_s`, `pwm_frequency_hz`, `sim_mode`, resolved log paths. Does not read `latest.json`. |
| **`/api/live`** | `latest.json` body plus feed-health fields (`feed_age_s`, `feed_trust_channel_metrics`, `telemetry_paths`, …). `Cache-Control: no-store`. |
| **`/api/history?minutes=&metric=`** | Downsampled series: `labels`, `channels`, `total`, optional `avg_target_ma` when `metric=ma`. |
| **`/api/stats`** | Per-channel today aggregates. |
| **`/api/daily`** | Today’s cumulative counters. |
| **`/api/sessions`** | Recent wet sessions. |
| **`/api/diagnostic`** | Raw `diagnostic_snapshot.json` or 404. |
| **`/api/export`**, **`/api/export/csv`** | File downloads. |

### `/api/live` highlights

- **`channels`**: string keys `"0"` … — each value includes legacy fields (`state`, `ma`, `duty`,
  `target_ma`, …) plus `reading_ok`, spec v2 fields (`state_v2`, `shift_mv`, …). See `logger.py`
  `public_channels` construction.
- **`feed_trust_channel_metrics`**: `false` when the feed is stale or `telemetry_incomplete` is set
  — UIs should avoid treating channel numbers as live CP truth in that state.

### `/api/meta` body (stable keys)

Use this on connect before the first `/api/live` to size the UI (channel count, tick rate).

## Control actions (not HTTP)

Use SSH (or another side channel) to run, for example:

- `iccp commission`, `iccp probe`, `iccp clear-fault`, `iccp diag --request`
- `sudo systemctl start|stop|restart iccp` (requires non-interactive sudo on the Pi if used from
  an app)

Optional: prefix with env exports for one shell, e.g.
`export COILSHIELD_LOG_DIR=/var/lib/iccp/logs && iccp live`.

## Versioning

`/api/meta` exposes **`package_version`** from installed **`coilshield-iccp`** metadata when
available. Bump **`pyproject.toml`** `[project].version` on releases so remote UIs can display it.
