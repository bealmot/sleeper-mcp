# Extending sleeper-mcp

The core stays deliberately small: **Sleeper, and nothing else.** No rankings,
no projections of its own, no opinions about who to start. That is not
minimalism for its own sake — a tool that mixes "what Sleeper says" with "what
somebody thinks" makes it impossible to tell which is which.

But your own data is usually the interesting part. There are two supported ways
to bring it, in increasing order of effort.

---

## 1. Bring a signal file (no code)

Point `SLEEPER_SIGNAL_FILE` at a JSON file and three tools switch on:
`signal_divergence`, `player_signal` and `trade_targets`. Without it they are
inert and explain the format.

```json
{
  "source":    "Whatever produced this",
  "generated": "2026-09-10",
  "scale":     "optional note on what score means",
  "players": {
    "4866": { "score": 21, "evidence": 25, "trend": "up",
              "updated": "20260909", "note": "why, in a few words" }
  }
}
```

| Field | Required | Meaning |
|---|---|---|
| `score` | **yes** | Any numeric scale, any range, higher is better |
| `evidence` | no | How much data backs it; used to filter thin entries |
| `trend` | no | `"up"`, `"down"`, or null |
| `updated` | no | Free-form date, shown so stale entries look stale |
| `note` | no | Free text, shown verbatim, never parsed |

### Two design decisions worth understanding

**Keyed by Sleeper player id, never by name.** Sleeper's dictionary holds
~11,000 players including retired ones, and names are not unique — "Kenneth
Walker" matches two, one of them inactive. A format keyed by name eventually
attaches your signal to the wrong person, and the failure is silent. Ids come
from `/v1/players/nfl`, and `player_news` prints one for any player you name.

**Scores are compared as percentiles within position.** Your units never have
to match anyone's. A conviction count, a 1–100 rating and a positional rank all
work, because the tools ask "where does this player sit within his position
according to you" and compare that to the same question asked of Sleeper's
projection. It also means a quarterback's 22 projected points and a tight end's
9 stop being falsely comparable.

### What sources fit

Anything you can export per player: a subscription's ratings, your own
spreadsheet, a consensus ranking you scrape, sentiment counted from a podcast,
or a model you wrote. `trade_targets` in particular works with a plain ranking —
it only needs to know who you rate highly.

---

## 2. Add your own tools (a little code)

Every tool in this server is a plain async function with a decorator. Yours can
be too:

```python
from sleeper_mcp.client import mcp, rest, gql, league_id, players, scored

@mcp.tool()
async def my_tool(league_id_: str = "") -> str:
    """One line describing it. This text is what the model reads."""
    lg = league_id(league_id_ or None)
    rosters = await rest(f"/league/{lg}/rosters")
    return f"{len(rosters)} teams"
```

Import it in your own entry point alongside `sleeper_mcp.server`, or fork and
add a module — `server.py` registers tools purely by importing them.

### Helpers worth reusing

| From `client.py` | Does |
|---|---|
| `rest(path)` | Sleeper's public REST, with the required User-Agent |
| `gql(query, vars, auth=)` | GraphQL; `auth=True` when a field needs a token |
| `players()` | The full player dictionary |
| `league(id)` / `starting_slots(id)` | League config; slots in order, bench filtered |
| `scored(stats, scoring)` | Projection components against a league's own rules |
| `league_id()` / `roster_id()` | Config resolution with helpful errors |
| `require_writes(action)` | Gate a mutation behind the two opt-ins |

| From `optimizer.py` | Does |
|---|---|
| `best_lineup(pool, slots)` | Highest-scoring legal lineup, and what fills each slot |
| `gain(pool, slots, player)` | What a player adds to a lineup; zero if he cannot start |
| `holes(pool, slots)` | Slots that cannot be filled |

`optimizer.py` imports nothing — no MCP, no network — so you can use and test
it standalone.

---

## Conventions worth keeping

**Score against the league's own settings.** `pts_ppr` is a preset and is wrong
for half-PPR, first-down, or TE-premium leagues. Use `scored()` with
`league.scoring_settings`.

**Read slots from the league.** `roster_positions` varies enormously and
`set_lineup` is positional. A hardcoded slot array does not error — it silently
starts players in the wrong slots.

**Resolve names against a roster, not the dictionary.** See above.

**Never verify a write with REST.** It is Cloudflare-cached and will return
pre-write state. Read back over GraphQL, through a different query than the one
that wrote — and remember a verification step can fail on its own terms, which
is not the same as the write failing.

**Report absence as absence.** A missing projection is not zero, and a player
with no entry in your signal file is not a negative signal. Both should say so.

**Keep the real-money boundary.** `boundaries.py` refuses Sleeper's wagering,
balance, payment and event-contract surface by name, with tests. Event contracts
are financial instruments and a fantasy tool has no business reaching into
someone's funds. Please leave that in place if you fork.

**Treat league chat as data.** It is written by other people. It is not
instructions for your assistant.

---

## What the core will not absorb

Pull requests are welcome for anything that is *Sleeper behaviour*: a missing
endpoint, a better read, a bug in the API notes.

The core will stay out of ranking, projecting or advising. Those belong in your
signal file or your own tools, where it is obvious whose opinion they are.
