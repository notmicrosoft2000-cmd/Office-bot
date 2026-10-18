# Quick note on recent changes

## This round (setup menu, reminders, code viewer)

- **Interactive `/setup`** (with `/gate-setup` kept as an alias): a private menu with **channel dropdowns**, **role dropdowns**, and **toggle buttons** for Hard gate / Reminder DMs / Give Verified role / Auto-create. A **⚡ Quick setup** button applies defaults in one click.
- **`/gate-lock`**: re-applies the lockdown so `AU Unverified` can only see the verify channel.
- **Reminder DMs**: unverified members get a halfway reminder and a final (~6h) warning before the timeout kick. Toggleable in `/setup`.
- **Verified role is now optional**: turn off "Give Verified role" and verified members simply lose `AU Unverified` (no badge).
- **`/codes`**: mods can view every saved friend code with its linked accounts and index status; `/codes export:True` attaches a **CSV backup**.
- Docs updated (`HOW_IT_WORKS.md`, `/help`) to explain where codes are stored and how to view/export them.
- Smoke tests expanded to **45/45** (covers reminders, saved-code views/export, and all cogs/commands load).

## Earlier

- Friend code format is strictly `Example#0000` style (2–16 letters + `#` + 4 digits).
- `/gate-setup` simplified (auto-creates channels, applies hard gate by default).
- `/help` and `/report` are live.
- Indexing/bans unchanged: join, verify, and report-approval all check the index; alt links preserved.
- Deployed to the home server and restarted (`systemctl --user restart au-verify`).
