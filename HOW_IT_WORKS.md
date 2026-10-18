# How this bot works (simple explanation)

The bot prevents known bad Among Us players from slipping back in. It uses **friend codes** as the anchor because friend codes are tied to the account and **can't be changed**. Since there's no official Among Us API, it doesn't "verify identity" — it records what code is claimed and stops anyone who matches the index.

## Core ideas

- **Join gate**: New members only see `#verify`. They must pick one of two options (submit friend code, or say "I don't play Among Us") within 48 hours, or they get kicked. On join they get a **welcome DM** and a **brief @ping in the gate channel** (that ping message is deleted right after so the channel stays clean). They also get a **reminder DM** halfway through and a **final warning** ~6h before the deadline (all toggleable).
- **Index (blacklist)**: A shared list of `friend_code` and/or `discord_id`. If anyone on that list joins or tries to verify, they're **banned immediately**.
- **Alt detection**: Every time someone verifies with a friend code, the bot links that code → that Discord account. If that code ever gets indexed, **every Discord account that ever used that code** is also banned. This catches alt accounts.
- **Verified role is optional**: On success the bot normally gives `AU Verified` and removes `AU Unverified`. Admins can turn the role grant off so verified members just lose the unverified role. `I don't play AU` members get `AU Non-Player`.
- **Anti-lie**: If someone clicks "I don't play Among Us" but later submits a friend code, the bot can **ban** them (default), **flag** them for mods, or ignore it (configurable).
- **Mod approval for reports**: `/report` lets anyone report an Among Us **friend code** (with a reason such as predatory behaviour). Reports go to a private mod queue with **Approve → blacklist** / **Reject**. **Only approved reports enter the index**. This stops people from maliciously framing others.
- **Persistence**: All links, reports, and the index live in `data/au_verify.db` on the server. Pending reports survive restarts.

## How to set it up (right now)

1. **Invite the bot** (already done) — it's `Office#6118` in your server.
2. **Run `/setup` in your server** (as an admin). An interactive menu opens in a private (ephemeral) message:
   - **Page 1 — Channels**: dropdowns to pick the verify channel, log channel, and report channel. Leave any blank to auto-create it.
   - **Page 2 — Roles**: dropdowns to pick the unverified/verified/non-player roles. Leave blank to auto-create.
   - **Page 3 — Options**: buttons to toggle **Hard gate**, **Reminder DMs**, **Give Verified role**, and **Auto-create missing**. Then press **Run setup**.
   - Shortcut: the **⚡ Quick setup** button fills in sensible defaults and runs immediately.
   - `/gate-setup` still works as an alias for `/setup`.
3. **Fix role order** (only if the bot warns you): Go to Server Settings → Roles and drag **Office** (the bot) **above** the three `AU *` roles, then re-run `/setup`.
4. **Done**. Try `/help` to see the full in-bot explanation anytime. Re-run `/setup` or `/gate-lock` whenever you want to re-apply the lockdown.

## What happens when you run `/setup`

Running it does these things in order:

1. **Makes/links roles** — Creates the three gate roles if they don't exist (or uses the ones you picked in the dropdowns), saves their IDs.
2. **Finds/creates channels** — Uses the channels you picked, or looks for `#verify`, `#mod-reports`, `#mod-logs` (case-insensitive) and creates them if missing. Report/log channels are made mod-only by default.
3. **Posts the gate message** — Deletes any old gate message and posts a fresh one in `#verify` with two buttons: **I play Among Us** (opens friend code modal) and **I don't play Among Us** (lets them through).
4. **Applies hard gate (if enabled)** — Sets `view_channel=False` for the `AU Unverified` role on every other text channel (except `#verify`). So new joiners literally can't see general chat until they pass. `/gate-lock` re-applies this any time.
5. **Saves everything** — Stores channel/role IDs and your toggles in the bot's database so it knows where to log, where reports go, whether to DM reminders, etc.
6. **Shows a summary** — Tells you what was created, where the gate message is, and any next steps.

## Where are the friend codes saved, and how do I view them?

- **On disk:** the SQLite database at `data/au_verify.db` on the server (`~/au-verify/data/au_verify.db`). The `members` table holds each member's current `friend_code`, and `code_links` holds every code↔account link ever seen. Treat this file as private.
- **In Discord (easy way):** run **`/codes`** (mods only) to page through every saved friend code with the linked account(s) and whether it's indexed.
- **Export a backup:** run **`/codes export:True`** and the bot attaches a CSV of every code↔account row. Keep a copy somewhere safe.
- **One user at a time:** `/whois user:@someone` shows all codes linked to that account.

## What the commands do (cheat sheet)

| Command | Who | What |
| --- | --- | --- |
| `/help` | Anyone | Explains how it all works (ephemeral) |
| `/setup` | Admin | Interactive setup menu (dropdowns + buttons); `/gate-setup` is an alias |
| `/gate-lock` | Admin | Re-apply the "Unverified can only see #verify" lockdown |
| `/verify` | Anyone (in server) | Re-opens the friend code form |
| `/report` | Anyone | Report a friend code (`Example#0000`) + reason → mod queue for approval/rejection |
| `/codes` | Mod | View every saved friend code (+ linked alts), or export a CSV |
| `/blacklist add/remove/list` | Mod | Manually manage the index |
| `/whois` | Mod | See a user's linked codes + index status |
| `/nuke` | Mod | Ban immediately + index all linked codes |
| `/ping` | Anyone | Gateway latency + uptime |
| `/stats` | Mod | Gate / index / report counters and the last audit event |

## What happens in practice (flows)

**A normal legit player joins:**
1. Joins server → gets `AU Unverified`, only sees `#verify`.
2. Clicks "I play Among Us" → types friend code → bot checks index. If clean, links code to them, gives `AU Verified`, removes Unverified. They can now see channels (if hard gate is on).

**A known bad actor (indexed) tries to join:**
1. Joins → bot checks their Discord ID against index. If matched, **bans them instantly**. No gate shown.

**A banned person makes an alt and tries with the same friend code:**
1. New Discord account joins → gets to gate.
2. Submits the indexed friend code → index hit → **banned immediately**. Also, if the code was linked to others, the system already would have caught them on join/verify.

**Someone reports a bad actor:**
1. Runs `/report` with the **friend code** (`Example#0000`) and a **reason** (predatory behaviour, harassment, ban evasion, …), then describes what happened and can attach evidence.
2. Mod sees it in `#mod-reports` → clicks **Approve → blacklist**. That adds the code to the index, **bans everyone currently linked to it**, and logs to `#mod-logs`.
3. Clicking **Reject** just closes it out.

**Someone lies about not playing:**
1. Clicks "I don't play Among Us" → passes gate as `AU Non-Player`.
2. Later submits a friend code via `/verify` → bot sees they previously claimed non-AU. With default `LIE_PUNISHMENT=ban`, it **bans them on the spot** and logs it.

## Limitations (be honest about these)

- **No official API**: Can't prove the code belongs to them. The value is the index + permanence + alt-linking.
- **Framing is possible**: Someone could report a legit person by submitting their code. That's why **mod approval is required** before adding to the index. Always check evidence in the report.
- **Friend code format**: Default requires `Name#0000` — 2–16 letters, a `#`, then exactly 4 digits (no dashes, not all digits). If your community uses a different format, tweak `FC_PATTERN` in `.env` on the server and restart.
- **Database is the source of truth**: `data/au_verify.db` contains Discord IDs and friend codes. Keep it private and backed up if you want.
