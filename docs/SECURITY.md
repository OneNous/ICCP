# Security hygiene (firmware repo)

## Secrets and `.env`

- Keep **`.env` gitignored**; use [`.env.example`](../.env.example) as the only committed template.
- On Raspberry Pi OS, prefer **`EnvironmentFile=`** in systemd (mode `0600`) over a world-readable `.env` in the project tree.

## SSH keys and other material that was ever committed

If a private key, token, or password was committed—even once—it may still exist in **git history** after a later delete.

1. **Rotate** the credential (new SSH key pair, new Supabase keys, etc.).
2. If the repository is shared or public, consider **history rewrite** (`git filter-repo` / BFG) with team coordination, then **force-push** and have collaborators re-clone.
3. Record the remediation in [`DECISIONS.md`](DECISIONS.md) (date + what was rotated).

This repo removed stray `pi` / `pi.pub` paths from the tree; **assume compromise until rotated**.
