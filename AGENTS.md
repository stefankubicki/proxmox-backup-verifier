# Proxmox Backup Verifier — Agent Instructions

## Branch workflow

This repo uses a two-branch model for public/private separation:

- **`main`** — public branch, mirrored to GitHub. Must not contain credentials or personal config.
- **`private`** — GitLab only. Contains `config.json` with personal rclone remote and local backup paths. Never mirrored to GitHub.

**Rules:**
- Merge `main` → `private` to keep the private branch up to date.
- Never merge `private` → `main`. This would leak personal config into the public branch.
- When working on public features or fixes, work on `main`.
- The `.gitignore` on `main` excludes `config.json`. The `.gitignore` on `private` does not.
