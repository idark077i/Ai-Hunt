# Scope — Proton

Authoritative scope is `memory/CONTEXT_LEDGER.md`. This file is a convenience
summary; the ledger wins.

## In scope

- `https://proton.me/`
- All first-party `*.proton.me` subdomains

## Out of scope

- `protonmail.com`, `proton.ch`, `protonvpn.com`, `proton.dev`, `proton.pink`
- Third-party CDNs, support portals, and marketing vendors

## Constraints

- 40 HTTP requests per rolling 60 seconds, combined
- At least 1.5 seconds between requests per worker
- Maximum 4 agents total; maximum 2 concurrent
- No races, DoS, spam, destructive writes, or real-user data
- No OOB (no Collaborator domain configured)
- Authenticated testing only via owned accounts in `login.md`
- No submission without master approval

## Current auth status

`login.md` is **NOT CONFIGURED**. Account A and B are placeholders. B-related
evidence contains third-party service emails, so B is paused until master
confirms ownership.
