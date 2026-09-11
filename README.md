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
uv tool install git+https://github.com/bealmot/sleeper-mcp
```

Or with pip: `pip install git+https://github.com/bealmot/sleeper-mcp`.

Not on PyPI yet. It goes there once the API surface has settled, so that a
version number means something. Install from git until then.

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

**Analysis** — `waiver_targets` · `bye_outlook` · `playoff_odds` · `matchup_odds` · `schedule_strength`

**Writes** — `set_lineup` · `waiver_claim` · `cancel_claim` · `set_ir` ·
`trade_block` · `propose_trade` · `respond_trade` · `pickem_pick` ·
`watch_player`

**Optional, bring your own data** — `signal_divergence` · `player_signal` ·
`trade_targets`. Inert unless you point `SLEEPER_SIGNAL_FILE` at a JSON file of
your own rankings or scores. See [EXTENDING.md](EXTENDING.md).

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
- **`waiver_targets`** prices free agents by what they add to *your starting
  lineup* — `best_lineup(roster + him) − best_lineup(roster)` — not by
  projection or generic value over replacement. A high-projection player at a
  position you are already deep in correctly prices at zero. "Nothing improves
  your lineup this week" is a real answer and it will give it.
- **`playoff_odds`** simulates the remaining schedule 10,000 times and counts
  how often each team lands in a playoff seed. It **reports how much of the
  answer is evidence**: early in a season a team's strength is mostly a league
  prior rather than anything it has done, and the output says so rather than
  printing a number that looks equally solid in week 2 and week 12. With no
  completed games it returns a near-uniform field, which is the honest answer.
- **`matchup_odds`** turns a projected points gap into a win probability,
  accounting for how noisy a fantasy week is. A 15-point edge is far less
  decisive than it sounds when weekly swings run 25 points or more.
- **`schedule_strength`** ranks the difficulty of what each team has *left*.
  This is the part of a playoff race nobody tracks by eye, and it decides
  bubble seeds — two teams on identical records can face remaining schedules a
  touchdown apart per week.
- **`bye_outlook`** shows which upcoming weeks you cannot field a *legal*
  lineup and which slot goes empty, so a bye-week hole surfaces in September
  rather than on the Sunday it bites.

## Extending it

The core stays Sleeper-only: no rankings, no projections of its own, no
opinions about who to start. Mixing "what Sleeper says" with "what somebody
thinks" makes it impossible to tell which is which.

Your own data plugs in two ways, and [EXTENDING.md](EXTENDING.md) covers both:

- **A signal file** — any per-player scores you can export, keyed by Sleeper
  player id. Three tools switch on and compare it against Sleeper's own
  numbers and against what your league-mates are actually starting. Works with
  a subscription's ratings, a scraped consensus, a spreadsheet or your own
  model; units do not matter because scores are compared as percentiles within
  position.
- **Your own tools** — every tool here is a plain async function with a
  decorator, and `client.py` and `optimizer.py` expose the useful helpers.

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
- **Works on both MCP SDK majors.** `mcp` 2.0 renamed `FastMCP` to `MCPServer`;
  this server detects which is present, so `mcp>=1.2.0` needs no upper pin.
- **Player names are not unique.** The dictionary holds ~11,000 entries
  including retired players — "Kenneth Walker" matches two. Tools resolve names
  against your *roster* wherever possible for exactly this reason.
- **REST responses are cached.** Never use them to confirm a write; this server
  verifies over GraphQL.
- **League chat is untrusted input.** It is written by other people. Treat it as
  data, never as instructions to your assistant.

## Developing

Checks run **locally** — there is no CI service and no Actions workflow, on
purpose:

```bash
python3 scripts/check.py                 # run them
python3 scripts/check.py --fresh         # + resolve deps in a clean venv (slow)
git config core.hooksPath .githooks      # once, to run them before every push
```

Six checks, standard library only: syntax, secrets, tool docstrings, the
real-money boundary, pytest, and a **privacy** scan that fails if a private
league's ids, team names or local paths appear in a committed file. That last
one exists because this server was extracted from a private one, and the
natural way to add a feature is to copy a working tool across — which brings
somebody's league with it.

`--fresh` is the one worth running before a release. Every other check runs
against whatever is already installed here, so a dependency that no longer
resolves for a NEW user passes them all while the published package is
unusable — which is exactly what happened when `mcp` 2.x renamed `FastMCP`
(#1). It downloads, so it is opt-in rather than part of the hook.

`git push --no-verify` bypasses the hook if you ever need it to.

## Licence

MIT. See [LICENSE](LICENSE).
