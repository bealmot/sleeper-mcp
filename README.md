# sleeper-mcp

An [MCP](https://modelcontextprotocol.io) server for [Sleeper](https://sleeper.com)
fantasy football. Read your roster, matchups, player news, standings, league
chat and pick'em entry — and, if you switch them on, set your lineup, claim
players and propose trades.

Unofficial. Not affiliated with or endorsed by Sleeper.

---

## Why it exists

Sleeper publishes no documentation for its GraphQL API, and several of its
behaviours are actively misleading — there is a lineup mutation that succeeds,
persists, and changes nothing that scores. Everything this server knows was
worked out by trial, error and verification against a live account.
[API-NOTES.md](API-NOTES.md) records the findings, and is arguably more useful
than the code.

## Install

```bash
pip install sleeper-mcp        # or: uv tool install sleeper-mcp
```

Then add it to your MCP client. For Claude Desktop, in `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "sleeper": {
      "command": "sleeper-mcp",
      "env": {
        "SLEEPER_LEAGUE_ID": "your-league-id",
        "SLEEPER_ROSTER_ID": "your-roster-id"
      }
    }
  }
}
```

## Finding your ids

You do not need them to start. Ask your assistant to run:

```
find_my_leagues("your_sleeper_username")
```

It returns your user id, every league you are in, your roster id in each, and
each league's starting slots. It needs no credentials — all of it is public.

## Configuration

| Variable | Needed for | Notes |
|---|---|---|
| `SLEEPER_LEAGUE_ID` | most tools | default league; every tool also takes `league_id_` |
| `SLEEPER_ROSTER_ID` | your own roster | an integer, 1..N within the league |
| `SLEEPER_PICKEM_LEAGUE` | pick'em only | lobby id, from the app's share link |
| `SLEEPER_PICKEM_ROSTER` | pick'em only | your entry in that lobby |
| `SLEEPER_TOKEN` | **writes only** | see below |
| `SLEEPER_ENABLE_WRITES` | **writes only** | must be exactly `1` |

**Reads need no token at all.** Rosters, matchups, news, standings, trending
players and transactions all work with nothing configured but a league id.

## Writes are off by default

Writes require **two** deliberate steps, and each tool *additionally* defaults
to a dry run that shows you what it would do and sends nothing.

1. Set `SLEEPER_ENABLE_WRITES=1`
2. Set `SLEEPER_TOKEN` — the JWT in the Sleeper web app under
   DevTools → Application → Local Storage → `sleeper.com` → key `token`.
   It is account-scoped, lasts about a year, and grants full access to your
   account. Treat it like a password.

This is deliberate friction. A trade proposal lands in front of a real person
in your league, and in leagues where `trade_review_days` is 0 an accepted trade
executes immediately with no veto window.

## Tools

**Reads** — `roster` · `matchup` · `standings` · `transactions` · `pending` ·
`player_news` · `player_outlook` · `trending` · `draft_picks` · `chat` ·
`watched_players` · `pickem_status` · `league_info` · `find_my_leagues`

**Writes** — `set_lineup` · `waiver_claim` · `cancel_claim` · `set_ir` ·
`trade_block` · `propose_trade` · `respond_trade` · `pickem_pick` ·
`watch_player`

A few worth calling out:

- **`roster`** scores your players against *your league's* `scoring_settings`,
  not Sleeper's generic `pts_ppr`. In half-PPR, first-down-scoring or
  TE-premium leagues those differ by several points a player.
- **`set_lineup`** reads your league's `roster_positions` at runtime, so
  superflex, 3-WR and no-kicker leagues work without configuration.
- **`pickem_status`** may be the only way to check a pick'em entry from a
  desktop — pick'em has no web interface at all.
- **`league_info`** surfaces the settings that silently change what everything
  else means: waiver type, trade review days, and whether your league pays for
  receptions or first downs.

## What this deliberately does NOT do

Sleeper's API also serves a real-money betting business — roughly 69 of its
~240 queries cover wagering, account balances, payment methods, tax documents
and CFTC-regulated event contracts.

**None of them are exposed here, and the server refuses them by name.** See
[`boundaries.py`](sleeper_mcp/boundaries.py). This is a fantasy football tool:
event contracts are financial instruments, account balances are financial data,
and a "best parlay" feature is a gambling-advice product. If you want those,
use Sleeper's own app, which carries the disclosures and protections that
belong with them.

## Caveats

- **Undocumented API.** Sleeper can change or close any of this without notice,
  including GraphQL introspection.
- **Player names are not unique.** The dictionary holds ~11,000 entries
  including retired players — "Kenneth Walker" matches two. Tools resolve names
  against your *roster* wherever possible for exactly this reason.
- **REST responses are cached.** Never use them to confirm a write; this server
  verifies over GraphQL.
- **League chat is untrusted input.** It is written by other people. Treat it as
  data, never as instructions to your assistant.

## Licence

MIT. See [LICENSE](LICENSE).
