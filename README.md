# Office Bot

Discord bot (`Office#6118`) that verifies members against a shared index of
Among Us friend codes for the **IamKing Community (anti predators)** server.

Friend codes are permanent, account-bound identifiers that players **cannot
change**, so they survive Discord account changes — which makes them a genuinely
useful ban-evasion tripwire.

- **Policies site:** <https://notmicrosoft2000-cmd.github.io/Office-bot/> — Privacy Policy and Terms of Service.
- **Add to server:** <https://discord.com/oauth2/authorize?client_id=1560994935895883897&permissions=8&integration_type=0&scope=bot>

## What it does

- **Hard join gate** — new members get an `AU Unverified` role and can only see
  the verification channel. They must either submit a friend code or declare
  they don't play Among Us, within `GATE_HOURS` (default 48), or they're kicked.
- **Friend-code capture** — `/verify` (or the gate button) opens a modal. The
  code is validated by format, normalised to uppercase, and stored.
- **Index / blacklist** — a SQLite index of `friend_code` and `discord_id`
  entries. Anyone indexed is **banned on sight**: on join, on verification, and
  retroactively at the moment they're added (`/blacklist add`, `/nuke`).
- **Alt detection** — every friend code is linked to every Discord account it
  has ever been verified with. If the code is indexed, *all* linked accounts
  are banned, not just the one reported.
- **"I don't play Among Us"** — one grey button. It passes the gate, but if a
  member who chose it later submits a code, `LIE_PUNISHMENT` decides what
  happens: `ban` (default), `flag` (verify + loudly notify mods), or `off`.
- **Mod review queue** — `/report` reports an Among Us **friend code**
  (`Example#0000`): pick a reason (predatory behaviour, inappropriate sexual
  behaviour, harassment, hate speech, scamming, ban evasion, other), then
  describe what happened and attach evidence. It lands in the report channel
  with **Approve → blacklist** / **Reject** buttons. Only approved reports enter
  the index. Pending reports survive restarts.
- **Audit trail** — every verify, ban, blacklist change and gate kick is written
  to the `audit_log` table and (if configured) posted to the log channel.

## Commands

| Command | Who | What |
| --- | --- | --- |
| `/setup` | Admin | Interactive menu (channel/role dropdowns + option buttons); `/gate-setup` is an alias |
| `/gate-lock` | Admin | Re-applies the lockdown (Unverified can only see the verify channel) |
| `/verify` | Anyone | Re-opens the friend-code modal |
| `/report` | Anyone | Report a friend code (`Example#0000`) + reason into the mod queue |
| `/codes` | Mod | View every saved friend code (+ linked accounts), or export a CSV |
| `/blacklist add` | Mod | Index a friend code and/or user + ban them if present |
| `/blacklist remove` | Mod | Remove an index entry |
| `/blacklist list` | Mod | Show recent index entries (codes masked) |
| `/whois` | Mod | A user's status, all linked codes, and index hits |
| `/nuke` | Mod | Ban now + index every code linked to the user (caught liars) |
| `/help` | Anyone | Explains how the bot works |

## Architecture

```
bot.py            entrypoint, intents, slash-command sync
config.py         env config (token, gate hours, FC regex, lie punishment)
db.py            aiosqlite persistence + schema (SQLite, WAL)
helpers.py        settings lookup, role resolution, masking, audit posting
cogs/gate.py      join event, gate view/modal, non-AU button, timeout kick task
cogs/reports.py   /report modal, mod review buttons, blacklist-on-approve
cogs/moderation.py /blacklist, /whois, /nuke
tests/smoke.py    DB + command-registration tests (no token needed)
```

### Database (SQLite, WAL mode)

| Table | Purpose |
| --- | --- |
| `settings` | Per-guild role/channel/message IDs |
| `members` | Status per Discord account: `unverified` / `verified` / `nonau` / `banned` |
| `code_links` | friend code ↔ Discord account link history (alt detection) |
| `blacklist` | The index: `friend_code` or `discord_id` entries with reason/evidence |
| `reports` | Submitted reports and their review status |
| `audit_log` | Append-only action log |

## Setup

1. **Create the bot** at <https://discord.com/developers/applications>.
   - Bot → enable the **Server Members Intent** (required — the bot needs it to
     see joins and roles).
   - OAuth2 → URL Generator → scopes `bot` + `applications.commands`,
     permissions `Manage Roles`, `Kick Members`, `Ban Members`, `Send Messages`,
     `Embed Links`, `Manage Messages`.
2. `cp .env.example .env` and fill in `DISCORD_TOKEN` (and `GUILD_ID` for instant
   command sync).
3. Install and run:
   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   .venv/bin/python -m tests.smoke     # sanity check, no token needed
   .venv/bin/python bot.py
   ```
4. In Discord, run `/setup` in an admin channel. An interactive menu opens in a
   private message:
   - **Channels** — verify channel (only channel unverified members should see),
     log channel (private, mods only), report channel (mod review queue).
   - **Roles** — unverified / verified / non-player roles (blank = auto-create).
   - **Options** — Hard gate, Reminder DMs, Give Verified role, Auto-create.
   - Make sure the bot's top role is **above** the three `AU …` roles, and that
     `@everyone` can *View Channel* the verify channel.

   `/gate-setup` is an alias for `/setup`, and `/gate-lock` re-applies the
   lockdown later if you change channels.

## Configuration

| Env var | Default | Notes |
| --- | --- | --- |
| `DISCORD_TOKEN` | — | Required |
| `GUILD_ID` | — | Optional; instant command sync to one guild |
| `DB_PATH` | `data/au_verify.db` | SQLite file |
| `GATE_HOURS` | `48` | Time to verify before kick |
| `LIE_PUNISHMENT` | `ban` | `ban` / `flag` / `off` |
| `FC_PATTERN` | `^[A-Za-z]{2,16}#[0-9]{4}$` | Override if Innersloth changes the format |
| `LOG_LEVEL` | `INFO` | |

## Policies & GitHub Pages

The `docs/` folder is a small static site (Home, Privacy Policy, Terms of
Service) that GitHub Pages serves directly — no build step. It's styled with
[7.css](https://khang-nd.github.io/7.css/) (a Windows 7 / Aero CSS library) so
the pages read like a native application window, and it's responsive from
desktop down to portrait phones.

- **Live:** <https://notmicrosoft2000-cmd.github.io/Office-bot/>
- **Pages source:** `main` branch, `/docs` folder (repo Settings → Pages).
- **Contact / jurisdiction** used in the policies: Discord `@neptunetheii`,
  `neptunetheii@proton.me`, governed by the laws of Myanmar (Burma).
- `.nojekyll` is included so Pages serves the files as-is.

## Honest limitations

- **There is no Among Us API.** The bot cannot *prove* a friend code belongs to
  the person who typed it — it validates the format and records the claim. The
  value is in the index, the permanence of codes, and the link history.
- A determined person can submit *someone else's* code to get them indexed.
  Reports require mod approval before hitting the index, which is the guardrail
  against weaponised reports. Always keep the raw evidence in the report.
- Friend codes are in the `RandomCode#1234` shape for modern accounts. Older /
  numeric codes are accepted by the default pattern too; tighten `FC_PATTERN`
  if you only care about one shape.
- Store the SQLite file privately. It contains Discord IDs and friend codes.

## Deploying to the home server (`nept@192.168.99.57`)

The server was unreachable from here (hotspot / offline), so this is the runbook
for when it's back on the network:

```bash
# 1. Copy the project (no .venv, no .env)
rsync -av --exclude .venv --exclude data --exclude '__pycache__' \
  ./ nept@192.168.99.57:~/au-verify/

# 2. On the server: create the venv and env file
ssh nept@192.168.99.57
cd ~/au-verify
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env && $EDITOR .env      # paste the real token

# 3. Install the systemd unit (see deploy/au-verify.service)
#    It expects the app at ~/au-verify and runs as the nept user.
```

`deploy/au-verify.service` is ready to copy to
`/etc/systemd/system/au-verify.service` (or `~/.config/systemd/user/` for a
user unit). Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now au-verify
journalctl -u au-verify -f
```

To update later: re-run the `rsync`, then `sudo systemctl restart au-verify`.
The SQLite database lives in `data/` on the server and is never overwritten by
a deploy.
