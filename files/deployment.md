# Deployment & OS Configuration

> **Scope:** This file covers what the Pi looks like as a deployed system — systemd, OS configuration, file system layout, install procedures.

## OS Choice: Raspberry Pi OS Lite (Bookworm)

The 64-bit Lite variant. No desktop. SSH enabled at first boot via the `ssh` file in the boot partition.

Why Lite:
- Smaller footprint, faster boot
- No GUI overhead
- Same packages available as the full version
- Validation devices don't need a desktop

Don't use Ubuntu, DietPi, or Pi OS Full. Don't use 32-bit (32-bit Pi OS exists for Pi 1/Zero compatibility, but we run Pi 3+ which all support 64-bit).

## Initial Setup Procedure (Per Device)

For each of the 10 validation units:

1. Flash Pi OS Lite 64-bit to a 32 GB SD card (Raspberry Pi Imager).
2. In the imager's customization, set:
   - Hostname: `coilshield-{serial}` (e.g., `coilshield-cs-2026-00001`)
   - Username: `onenous` (consistent across all units)
   - SSH enabled with public key auth
   - WiFi: leave blank (BLE provisioning sets it)
   - Locale and timezone: UTC
3. Insert SD card, boot Pi.
4. SSH in (`onenous@coilshield-cs-2026-00001.local`)
5. Run the install script:

```bash
curl -sSL https://raw.githubusercontent.com/OneNous/coilshield-firmware/main/install.sh | bash
```

The install script:
- Updates apt packages
- Installs system dependencies (python3-pip, bluez, avahi-daemon, etc.)
- Clones the firmware repo
- Installs Python dependencies
- Configures sudo permissions for required commands
- Installs the systemd unit file
- Configures Avahi for mDNS
- Enables 1-Wire, I2C, SPI via raspi-config nonint commands
- Reboots

After reboot, the device is in unprovisioned state, advertising BLE, ready for the tech app.

## Rule DEP-1: systemd Manages the Process

The firmware runs as a systemd service. Unit file at `/etc/systemd/system/coilshield.service`:

```ini
[Unit]
Description=CoilShield ICCP Control
After=network.target wpa_supplicant.service

[Service]
Type=simple
User=onenous
WorkingDirectory=/home/onenous/coilshield-firmware
EnvironmentFile=/etc/coilshield/env
ExecStart=/usr/bin/python3 /home/onenous/coilshield-firmware/src/main.py --real
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=coilshield

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl enable coilshield.service
sudo systemctl start coilshield.service
```

Logs to journalctl:

```bash
journalctl -u coilshield -f          # follow live
journalctl -u coilshield --since "1 hour ago"
```

## Rule DEP-2: Environment Variables in /etc/coilshield/env

Sensitive config and per-device settings:

```bash
# /etc/coilshield/env (mode 600, owned by onenous)
SUPABASE_URL=https://xxxxx.supabase.co
SUPABASE_SERVICE_KEY=eyJ...
DEVICE_ID=uuid-here
DEVICE_SERIAL=CS-2026-00001
COILSHIELD_SIM=0
LOG_LEVEL=INFO
```

The systemd unit references this file via `EnvironmentFile=`. Don't commit this file to git. Don't print these variables in logs.

## Rule DEP-3: File System Layout

```
/home/onenous/coilshield-firmware/    # Repo clone
/etc/coilshield/                      # Config (env file)
/var/lib/coilshield/                  # Runtime state
  ├── bonded_devices.json
  ├── local.sqlite                    # Local DB
  └── latest.json                     # Most recent state
/var/log/coilshield/                  # Logs (also goes to journalctl)
  ├── fault.log
  ├── tech_api.log
  └── bled.log
```

systemd's StateDirectory and LogsDirectory directives can manage some of this automatically — see if they apply.

## Rule DEP-4: SD Card Lifespan

SD cards die from write wear, especially under heavy logging. Mitigations:

- Logs to journalctl are persisted (the default), but configurable to RAM-only via `Storage=volatile` in `journald.conf` if write volume gets concerning
- The local SQLite DB uses WAL mode (already configured in `logger.py`)
- Don't add log statements in tight loops

For 10 validation units running 24/7, write volume is moderate. The SD card should last 1+ years without issues. If it doesn't, switch to industrial-grade SD cards (e.g., SanDisk High Endurance) for production.

## Rule DEP-5: Updates During Validation

When firmware updates are needed during validation, the procedure is:

1. SSH to the device
2. `cd ~/coilshield-firmware`
3. `git pull`
4. `pip install -r requirements.txt` (if deps changed)
5. `sudo systemctl restart coilshield`
6. Verify via `journalctl -u coilshield -f` that it came back up cleanly
7. Verify via the command center that readings resume

This is manual. Post-validation, an OTA update mechanism becomes important. Don't build it during validation — it's not on the critical path.

## Rule DEP-6: Backup Strategy

Each device's local data should be backed up periodically. The owner runs a script from their laptop:

```bash
# backup_devices.sh
DEVICES=("coilshield-cs-2026-00001" "coilshield-cs-2026-00002" ...)
DATE=$(date +%Y-%m-%d)

for dev in "${DEVICES[@]}"; do
    rsync -avz "onenous@${dev}.local:/var/lib/coilshield/" \
              "./backups/${dev}/${DATE}/"
done
```

Run this weekly during validation. Cloud sync covers most of the data, but local-only state (bonded_devices, pending_uploads) needs explicit backup.

## Rule DEP-7: Time Sync

The Pi's clock is set via NTP at boot. Ensure NTP is running:

```bash
sudo systemctl enable systemd-timesyncd
sudo systemctl start systemd-timesyncd
```

If NTP can't reach servers (firewall, captive portal, etc.), the clock may drift. The cloud sync code includes a clock check that detects this — see `.claude/cloud-sync.md` Rule CS-6.

## Rule DEP-8: Required raspi-config Settings

Run via raspi-config nonint commands in the install script:

```bash
sudo raspi-config nonint do_i2c 0          # Enable I2C
sudo raspi-config nonint do_onewire 0      # Enable 1-Wire (DS18B20)
sudo raspi-config nonint do_serial 1       # Disable serial console (we don't use it)
sudo raspi-config nonint do_camera 1       # Disable camera (not used)
```

Verify after install:

```bash
ls /dev/i2c-*                              # Should show /dev/i2c-1
ls /sys/bus/w1/devices/                    # Should show 1-wire devices
```

## Rule DEP-9: Sudo Permissions for Specific Commands

The firmware needs sudo for specific operations (writing wpa_supplicant.conf, restarting wpa_supplicant). Configure via `/etc/sudoers.d/coilshield`:

```
onenous ALL=(ALL) NOPASSWD: /usr/sbin/iw wlan0 scan
onenous ALL=(ALL) NOPASSWD: /usr/bin/tee -a /etc/wpa_supplicant/wpa_supplicant.conf
onenous ALL=(ALL) NOPASSWD: /bin/systemctl restart wpa_supplicant
onenous ALL=(ALL) NOPASSWD: /bin/systemctl restart bluetooth
onenous ALL=(ALL) NOPASSWD: /bin/systemctl restart coilshield
```

Permissions on the file:

```bash
sudo chown root:root /etc/sudoers.d/coilshield
sudo chmod 440 /etc/sudoers.d/coilshield
sudo visudo -c -f /etc/sudoers.d/coilshield   # Validate syntax
```

Don't grant blanket sudo. Only the specific commands needed.

## Rule DEP-10: Watchdog

Pi has a hardware watchdog (BCM2835). Enable it:

```bash
# In /boot/config.txt
dtparam=watchdog=on

# Configure systemd to use it
# In /etc/systemd/system.conf:
RuntimeWatchdogSec=15s
RebootWatchdogSec=10min
```

This means: if userspace hangs (kernel panics, deadlocks), the watchdog reboots the Pi automatically. systemd's runtime watchdog (15 seconds) catches process-level hangs.

The control loop should call `sd_notify(WATCHDOG=1)` periodically to indicate health:

```python
# In control.py main loop
import sdnotify
notifier = sdnotify.SystemdNotifier()
notifier.notify("READY=1")

while True:
    # ... control loop work ...
    notifier.notify("WATCHDOG=1")
```

If the loop hangs, watchdog times out, systemd kills and restarts. If systemd hangs, hardware watchdog reboots the Pi.

## Rule DEP-11: First-Boot Provisioning (Manufacturing)

For each device that ships, the manufacturing process is:

1. Flash SD card with the customized image (above)
2. First boot — runs install script
3. Generate device serial: print sticker, label hardware
4. Set DEVICE_SERIAL and DEVICE_ID in `/etc/coilshield/env`
5. Register the device in the prod Supabase project (manual SQL insert during validation)
6. Power off, ship to tester (or install yourself)

For 10 units, this is manual and acceptable. For more units, automate with a "first-boot wizard" script that prompts for serial number.

## Rule DEP-12: Logs Rotation

Without log rotation, logs eventually fill the SD card. Configure logrotate for our app logs:

```
# /etc/logrotate.d/coilshield
/var/log/coilshield/*.log {
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
}
```

journalctl rotates automatically based on `journald.conf` settings. Default is fine for validation.

## Common Cursor Pitfalls in Deployment Code

- Suggesting Docker (overkill for a Pi running one Python process)
- Using `pip install --user` (we install at the system level for the systemd service)
- Suggesting Ansible/Salt for 10 units (manual SSH + scripts is sufficient)
- Forgetting that systemd kills processes that don't notify watchdog (test the watchdog notification path)
- Hardcoding paths instead of using environment variables (the `/etc/coilshield/env` pattern is intentional)
- Suggesting custom init scripts (systemd is the standard, use it)

## Smoke Test for Deployment

Before declaring deployment "validation-ready":

1. Fresh SD card with customized image boots, runs install script, reboots cleanly
2. systemd shows `coilshield.service` as active and running
3. `journalctl -u coilshield` shows expected startup messages
4. Manually `sudo systemctl restart coilshield` — service restarts cleanly within 15 seconds
5. Manually `kill -9` the Python process — systemd restarts it within 15 seconds
6. Hardware watchdog test: forcibly hang the Python process (a deliberately bad debugging test) — Pi reboots within 10 minutes
7. SD card with full backup can be flashed to a replacement Pi and run identically
8. SSH in via mDNS using `onenous@coilshield-{serial}.local` works
9. Logs rotate properly (manually trigger and verify)
10. Reboot device — comes back online, BLE re-advertises if WiFi is gone, otherwise reconnects to WiFi and resumes pushing data

If any step fails, deployment is not validation-ready.
