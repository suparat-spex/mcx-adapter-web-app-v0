# Web console

    python -m mcx_web PROJECT.mcx            # console http://127.0.0.1:8810, MCP endpoint per the project (8811)
    python -m mcx_web --demo                 # the demo plant
    python -m mcx_web PROJECT.mcx --host 0.0.0.0 --cert site.pem --key site.key   # reachable on the plant network, HTTPS

One process runs the adapter (sources, MCP endpoint) and the console. Every change made in the console
is applied at once, saved to the project file and written to the audit log with the user's name
(Audit log > Show: Console changes & sign-ins).

## Accounts (local, on-premise)

- First visit with no accounts: create the first **admin** (or `python -m mcx_web PROJECT --create-admin NAME`).
- Users live in `mcx_web.db` (SQLite) next to the project. Passwords: PBKDF2-SHA256, 240k iterations.
- Roles: **Admin** - users, API keys, MCP endpoint, licence (+ everything below); **Engineer** - sources,
  discovery, tools, profiles, limits, publish, test calls; **Viewer** - read only.
- At least one active admin is always kept. Five wrong passwords lock the account for 5 minutes.
  Disabling a user or setting a new password ends their sessions. Sessions last 12 h of inactivity.
- Forgotten admin password: `python -m mcx_web PROJECT --create-admin rescue` on the server.

## Security

Session cookie HttpOnly + SameSite=Strict (+ Secure over HTTPS); a CSRF token in every form; the role is
checked on every request; Content-Security-Policy without inline scripts; no CDN or web fonts (works
offline in the plant). API key secrets are shown once. Put the console on HTTPS (`--cert/--key` or a
reverse proxy) whenever it is reachable beyond the server itself.
