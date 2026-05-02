# Security — credentials and repository hygiene

This document supports [`docs/DECISIONS.md`](docs/DECISIONS.md) entries on credential exposure. It is **not** a substitute for rotating compromised material.

## If a private key or API secret was ever committed

1. **Treat the material as burned** until you rotate it: generate new SSH keys, rotate Supabase service keys in the project dashboard, and revoke any tokens that appeared in history.
2. **Removing the file from `main` does not remove blobs** from clones, forks, or cached CI artifacts. Anyone with an old clone can still run `git log` / `git show` on the object.
3. **Optional history rewrite** (coordinate with everyone who uses the remote): use [`git filter-repo`](https://github.com/newren/git-filter-repo) or BFG Repo-Cleaner, then **force-push** protected branches. Every collaborator must re-fetch or re-clone; open PRs may need rebasing.
4. **If you cannot rewrite history** (public forks, release tags): rotation plus monitoring is the practical mitigation; assume the secret leaked.

## Firmware-specific rules

- **Supabase service role key:** never commit, log, expose over BLE, or return from HTTP. Prefer systemd `EnvironmentFile=` with mode `600` on the Pi (see `.claude/cloud-sync.md`).
- **Tech app bond key:** `COILSHIELD_TECH_BOND_KEY` is bench/local until BLE bond storage exists; treat like any shared secret.
- **No secrets in committed `.env`** — use `.env.example` with placeholders only.

## Reporting

Use your team’s normal security channel for suspected active compromise of production Supabase or devices.
