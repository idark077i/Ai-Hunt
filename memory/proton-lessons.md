# Proton Hunt — Durable Lessons

**Created:** 2026-10-27T15:50Z
**Use:** read this before resuming the Proton target after a restart.

## Scope and rules

- Scope is `proton.me` plus first-party `*.proton.me`. Exclude
  `protonmail.com`, `proton.ch`, `protonvpn.com`, `proton.dev`, `proton.pink`,
  and third-party vendors.
- `shop.proton.me` (Shopify) and `status.proton.me` have a scope conflict in the
  old notes. Keep them out until the master resolves it.
- Hard limit: 40 HTTP requests per rolling 60s combined; 1.5s minimum spacing;
  max 4 agents total and max 2 concurrent.
- Unauthenticated testing is allowed. Authenticated testing requires owned
  Account A and Account B in `login.md`, plus confirmation that B is owned.
- No OOB tests without a user-provided Collaborator domain.
- No report, PoC, or submission without validation and master approval.

## Authentication state

- `login.md` still contains placeholder account emails. Do not log in.
- B-related old evidence contains third-party service emails (Hubstaff,
  Spendesk, Doit). Do not use B until the master confirms ownership.
- Browser contexts are isolated:
  - `chrome-devtools` = Account A
  - `chrome-devtools-b` = Account B
- Both use Brave Flatpak with `--isolated`; they must log in again each session.
- `chrome-devtools-mcp` requires
  `--executablePath /var/lib/flatpak/exports/bin/com.brave.Browser`.
  It ignores `CHROME_PATH` and `CHROME_DEBUGGING_PORT`.

## Request-ledger discipline

- `testing/proton.me/www/notes/rate-limit.md` must be **append-only**.
- A pass 2 agent overwrote it; pass 1 rows were reconstructed from the pass 1
  command log. Do not let a subagent use `write` on this file.
- Every target request must record UTC timestamp, method, URL, status, and
  account/context.
- Current logged total: ~60 target HTTP requests across passes 1-15; last at
  `2026-10-27T18:57:13Z`. See `rate-limit.md` for exact rows.

## Results so far

- No **approved** reportable finding. One validated candidate:
  **Lumo guest limit bypass**.
  - `https://lumo.proton.me/api/ai/v1/{models,limits,chat/completions}` is a
    public guest API. `x-pm-appversion: web-lumo@2.0.4.1` is required; a
    plaintext OpenAI body is accepted with no auth, cookie, or U2L encryption.
  - Guest limits are per client-controlled `Session-Id`. Jars A/B/C each reset
    from 20 to 19; a no-cookie `lumo-max` request returned 200 with `max: 19`
    and a fresh session. Ignoring `Set-Cookie` gives a new 20-message quota per
    request. No IP throttle observed across 7+ no-cookie chats.
  - Impact is free use of paid AI models/tools beyond the guest quota; no data
    access or ATO. Treat as Medium business logic if per-session quota is not
    intended; Low/expected if it is. See
    `notes/07-GUEST-LIMIT-BYPASS.md`. No report without master approval.
  - `web_extract` blocks loopback, private IPs, and redirects to loopback;
    CORS from `evil.example` has no ACAO/ACAC; `lumo-preview` returns 503.
  - The `lumo-max` request is charged to the `max` pool but the SSE response
    `model` field says `lumo-lite`; actual backend routing is unconfirmed.
- Subdomain takeover: negative.
  - `rust-registry.proton.me` -> legitimate GitHub Pages.
  - `localize.proton.me` -> active Discourse.
  - `partners.proton.me` -> legitimate login.
  - `verify.proton.me` -> human verification.
  - `wallet.proton.me` -> landing page.
  - `sheets.proton.me` -> redirect to Docs.
  - `reports.proton.me` -> Proton 404.
  - `autofill.reports.proton.me` -> timeout; ELB DNS resolves; not a takeover.
- CORS: negative on account users/auth-info; no ACAO reflection, no ACAC.
- Source maps: production maps with `sourcesContent` are public on first-party
  apps. No hardcoded secrets found. **INFO only.** Do not report alone.
- Lumo: landing page is a marketing shell; version 2.0.4.1; CSP exposes
  `wss://lumo-api.proton.me`; API code is in lazy chunks or the source map.

## Model and tooling notes

- `openrouter/nvidia/nemotron-3-ultra-550b-a55b:free` works for PentestCode.
- `openrouter/openrouter/free` also works as a fallback.
- `opencode/deepseek-v4.1-flash-free` and `opencode/glm-5.3-flash-free` timed
  out in PentestCode; do not use them for target work.
- The free model can be slow. Use short, bounded prompts and save logs under
  `logs/sessions/` (not `/tmp`, which is cleared on restart).
- A 240s foreground timeout may kill the agent after it finishes the fetches but
  before it writes the summary. Save evidence incrementally and review it
  locally.

## Workspace paths

- State: `testing/proton.me/www/notes/00-STATE.md`
- Plan: `testing/proton.me/www/notes/02-NEXT-TESTS.md`
- Mindmap: `testing/proton.me/www/MINDMAP.md`
- Route inventory: `testing/proton.me/www/notes/js-01-route-map.md`
- Ledger: `memory/CONTEXT_LEDGER.md`
- Safe evidence copies: `testing/proton.me/www/evidence/redacted/`
- Never print or share unredacted PGP/private-key evidence.

## Server restart recovery

1. Read this file and `notes/00-STATE.md`.
2. Check `notes/rate-limit.md` for the last request time.
3. Check `evidence/` for partial artifacts from the interrupted pass.
4. Do not repeat completed fetches; verify file size and status first.
5. Append a new pass section; never overwrite.
