# SECRETS POLICY — BogiAgent

**Date:** 2026-05-31 · Scope: how secrets live, what never gets committed/logged,
and the path to encrypted-at-rest secrets. Distilled from jarvis-core's
secrets-management design, sized down for a solo personal project.

## Principles

1. **`.env` is local-dev only.** It holds decrypted runtime secrets and is
   **never committed** (already in `.gitignore`).
2. **`.env.example` holds placeholders only** — no real values, ever. It is the
   committed contract of which keys are needed.
3. **Tokens / cookies / browser profiles / OAuth files are never committed.**
   They live under `data/` (gitignored): `data/gcal/`, `data/browser_profiles/`,
   and `~/.claude/.credentials.json` outside the repo.
4. **Logs must redact secrets.** No `Authorization` headers, API keys, OAuth
   tokens, cookies, or raw private bodies (email/message text) in logs or traces.
   See `OBSERVABILITY_PLAN.md` for the scrub list.
5. **Pre-commit blocks obvious secrets** — the `.githooks/pre-commit` Tier-3 guard
   already blocks `.env`/secret/key/token/`data`/`vault` paths. Extend it with a
   content scanner (see below) so a secret pasted into a *normal* file is caught.

## Required secrets (keys, not values)

| Key | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` / claude.ai OAuth | Claude via LiteLLM |
| `LITELLM_MASTER_KEY` | LiteLLM proxy auth |
| `POSTGRES_PASSWORD` | DB |
| `TELEGRAM_BOT_TOKEN` | Telegram bot |
| `OPENAI_API_KEY` | fallback model + Whisper |
| Google OAuth client + token | Calendar (under `data/gcal/`) |

## Now (cheap, high value) — DONE (PHASE 3, 2026-05-31)

- **Content scanner in pre-commit** ✅ — `.githooks/precommit_check.py` scans
  staged added lines for `sk-ant-`/`sk-`/`ghp_`/`xox*-`/`ya29.`/`AKIA…`/PEM and
  blocks the commit; skips `.env.example`/`docs/`/`tests/`. Stdlib-only.
- **Log redaction** ✅ — `bogi/redaction.py` (`redact()` + `RedactingFilter`)
  wired into `cli._setup_logging`; secrets are scrubbed from console + `bot.log`.
  Logfire spans are scrubbed separately in `observability.py`.
- **`.env.example` audit** ✅ — placeholders only; every key documented.

## Future path (only if it earns its keep) — sops + age

Encrypt secrets at rest so a backup or repo mirror never leaks them:

```
.sops.yaml              # committed; contains the age PUBLIC key
secrets/secrets.enc.yaml # committed; sops-encrypted (safe at rest)
.env                     # gitignored; decrypted at dev start
~/.config/sops/age/keys.txt  # PRIVATE key — never committed, never shared
```

Setup (once): `winget install FiloSottile.age mozilla.sops`;
`age-keygen -o keys.txt`; put the public key in `.sops.yaml`.
Daily: `Decrypt-Secrets.ps1` → `.env`. Edit: `sops secrets/secrets.enc.yaml`.
Rotate: new age key → `sops updatekeys` → re-commit `.enc.yaml`.

**Trade-off:** sops+age adds friction (tool installs, decrypt-before-run) for a
solo dev. Adopt only if secrets must survive in a synced/backed-up repo, or a
second machine/CI enters the picture. Until then, the scanner + redaction above
cover ~90% of the real risk.

## Hard rules (never break)

- Never commit `.env`, `data/`, `vault/`, OAuth tokens, cookies, browser profiles.
- Never log or trace a secret value or a raw private body.
- The age private key (if adopted) is never committed or shared.
- CI/remote (if ever) injects secrets via env vars, not committed files.
