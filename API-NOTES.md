# Notes on Sleeper's undocumented API

Sleeper publishes no documentation for its GraphQL endpoint. Everything below
was established by testing against a live account, and most of it cost a real
mistake first. It is recorded here because the findings are more durable than
this particular implementation.

## The two APIs

```
REST      https://api.sleeper.app/v1    public, unauthenticated, CACHED
GraphQL   https://sleeper.com/graphql   undocumented, partially authenticated
```

Introspection is **open** on the GraphQL endpoint: ~240 queries and ~349
mutations. Note that introspection uses **snake_case** (`query_type`,
`of_type`, `input_fields`), not the usual camelCase.

## Traps

### Sleeper 403s if you send no User-Agent

Not a friendly error — a flat refusal that reads like an IP block. Send one.
(MyFantasyLeague, for contrast, 403s if you *do*. They are not the same shape;
do not share a request helper between them.)

### REST is Cloudflare-cached and cannot verify a write

A roster read can return the pre-write state for minutes, complete with
`cf-cache-status: HIT` and an `Age` header. It is fine for player dictionaries
and league config. **Never** use it to confirm a mutation landed.

### `roster_update_starters` is the wrong mutation

It exists, it succeeds, it persists, and **it changes nothing that scores**. It
sets a roster-level field that the scoring engine does not read. The weekly
lineup lives in the matchup leg:

```graphql
update_matchup_leg(round: $wk, leg: $wk, league_id: $lg,
                   roster_id: $rid, starters: [String])
```

The web app sends exactly this. It also passes `starters_games` (a `Map`), but
sends it empty, and calls succeed without it.

### Timestamps are milliseconds

`published` on news, `created` on messages. Parsed as seconds they date content
to the year 58,000-odd, which looks like a parsing bug rather than a unit one.

### `messages(order_by: "created")` returns HTTP 500

The argument is a **direction** (`"asc"`), not a field name, and an invalid
value crashes the server rather than erroring cleanly. Omit it and sort
client-side.

### `watch_player` and `unwatch_player` have different return types

```
watch_player    -> Player   (OBJECT — requires a subfield selection)
unwatch_player  -> Boolean  (SCALAR — must NOT have one)
```

One shared query template cannot serve both.

### Several *reads* are authenticated

`matchup_legs` and `messages` return `Unauthorized` without a token, unlike
most league queries. A read-back that throws is therefore not evidence that a
write failed — distinguish "the write failed" from "the check failed".

### `k_`/`v_` parallel arrays

`submit_waiver_claim` and `propose_trade` take paired arrays rather than
objects:

```graphql
submit_waiver_claim(league_id: Snowflake!,
                    k_adds: [String], v_adds: [Int],
                    k_drops: [String], v_drops: [Int],
                    k_settings: [String], v_settings: [Int])
```

`k_adds` holds player ids, `v_adds` the roster receiving each, and
`k_settings`/`v_settings` carry `["waiver_bid"]` / `[amount]`.

### Player names are not unique

The dictionary holds ~11,000 entries **including retired players**. "Kenneth
Walker" matches two — one active, one with `team: null, active: false`.
Submitting the inactive one produces:

> There may be a starter who is no longer eligible for the slot they are
> assigned to.

That message is accurate and easy to misread as a lock or a deadline. It means
`active: false`. Resolve names against a **roster**, not the dictionary.

### `add_league_player_note` exists but is refused

Schema-valid, and every correctly-formed call returns:

> Sorry, we were not able to add notes to this player.

Identically for rostered players and free agents, short notes and long. Also
note `LeaguePlayer` exposes only `metadata/settings/player_id/league_id` —
there is no `note` field to select despite the mutation taking one.

### IR is a subset of the roster

A player on IR appears in **both** `players` and `reserve`, and does not render
on the bench because he occupies the IR slot. Not a bug.

### The team-page restriction is UI-only

Sleeper only renders lineup controls on the `/team` route — you cannot swap a
player from `/matchup` by hand. The API does not care.

## Drafts

`GET /draft/{draft_id}/picks` gives the board, with `is_keeper` marking keepers
and the round and pick each cost. `GET /draft/{draft_id}` gives the format —
snake or auction, rounds, timers, roster slots.

**`roster_draft_picks(league_id, season?)` lists only the EXCEPTIONS.** It
returns picks that have CHANGED HANDS, not every pick a team holds, so an empty
result means nothing has been traded rather than that the query failed. Asking
for a future season with no trades yet returns `[]`.

**Scoring a draft is a positional question.** Ranking every drafted player
together by raw points and comparing with pick number looks reasonable and is
badly wrong: quarterbacks outscore running backs and receivers by a wide margin
in most formats, so every late quarterback comes out an enormous steal and
every early receiver a bust. A first pass at this returned a "best picks" list
that was nothing but quarterbacks and defences — a fact about the scoring
system, not about anybody's drafting. Compare a pick with the players taken
ahead of it AT THE SAME POSITION instead.

Live-draft endpoints — `draft_pick_player`, `update_draft_queue`,
`draft_nominate_player`, `draft_offers` — only do anything while a draft is
actually running, which is one week a year.

## `roster_standings` — the table as it stood

Authenticated. `roster_standings(league_id, round)` where **`round` is the
WEEK**, and both arguments are required. It returns nothing for a week that has
not finished, which is why an in-season league in week 1 gives an empty list
for every round and looks broken.

REST gives only the current totals, so this is the only place a season's SHAPE
is recorded. Two fields earn it:

- **`rank`** as of that week, so the whole trajectory is recoverable.
- **`record` is a STRING of results in order** — `"LWWWW"` is an opening loss
  then four straight wins, and its length is the games played. Nothing else in
  the API gives form or a streak.

A 7-6 team on a five-game winning run and a 7-6 team on a five-game losing one
are the same row in the standings and opposite propositions in a trade.

`RosterStanding` also carries `correct_picks`, `total_picks`, `correct_bans`
and `total_bans`. Those are pick'em fields on a shared type and come back null
for a fantasy league.

## Pick'em

All authenticated. Pick'em has no web interface at all, so these endpoints are
the only desktop access to a pool.

**`outcome` IS NOT A RESULT.** Every pick carries `outcome: "win"` — 1,283 of
1,283 across a live 159-entry pool, winners and losers alike. It records which
way the pick points. Scoring from it rates every entrant perfect, and the error
is invisible, because a pool where everyone is flawless still renders as a
tidy table. Nothing in these endpoints says who is winning.

**`leg_id` is `"v1:regular:<week>"`**, not a snowflake. Get the list from
`get_pickem_legs(league_id, roster_id)`; both arguments are required.

**`get_pickem_picks_for_league(league_id, leg_id, include_tiebreaker)`** returns
a Map keyed by roster id as a STRING, each value `{picks, tiebreaker}` where
`picks` is `{game_id: {team, outcome, updated_at, game_id}}`.

**Most entries are empty.** In that pool 69 of 159 had no picks at all, so a
share computed against the entry count rather than the submitting count
understates every split by nearly half.

**Entries outnumber users.** REST reported 73 users against 159 rosters for the
same pool — one person may hold several entries, so roster ids do not map
one-to-one onto people.

**`get_pickem_scoring_settings(league_id)`** returns `{leg_id: points}` for the
whole season at once, which is how you spot a pool that weights later weeks.

## `league_transactions_filtered` — and what REST hides

Authenticated. `league_id` is required; `type_filters`, `status_filters`,
`leg_filters` and `roster_id_filters` are all lists and compose as you would
expect.

**It shows transactions that never happened, and REST does not.** Compared over
one full season of the same league:

| | REST | GraphQL |
|---|---|---|
| free_agent / complete | 207 | 207 |
| waiver / complete | 62 | 62 |
| trade / complete | 4 | 4 |
| trade / cancelled | **0** | 20 |
| trade / rejected | **0** | 12 |
| waiver / cancelled | **0** | 29 |
| waiver / failed | 29 | 13 |

Completed transactions agree exactly. Everything else does not, and **neither
source is a superset**: by transaction id, 61 were in GraphQL only and 16 in
REST only (failed waiver claims), with no id carrying a different status in the
two. To see a week completely you need both.

The gap matters because it changes what the data means. That league proposed
36 trades and completed 4; a completed-only list contains the four and cannot
distinguish a quiet league from one where nobody accepts.

**A trade puts every player in BOTH `adds` and `drops`,** since he moves
between rosters. Printing the two lists separately shows each player twice,
once arriving and once leaving. Group by destination instead.

**Traded draft picks come back as COMMA-SEPARATED STRINGS here** and as objects
from REST. Decoded from both forms of one transaction:

```
GraphQL  "9,2026,6,5,9"
REST     {roster_id: 9, season: "2026", round: 6, owner_id: 5,
          previous_owner_id: 9}
```

so the order is `roster_id, season, round, owner_id, previous_owner_id`. Code
expecting the object drops the string silently, and a trade whose other half
was a pick then renders as one side receiving nothing.

## Keepers: two fields, different answers

`roster.keepers` and the draft's `is_keeper` picks are NOT the same thing, and
they disagree — which makes either one, read as "the keepers", confidently
wrong about the other.

In the league this was developed against, the 2026 draft carried **eight**
`is_keeper` picks — McCaffrey r1p5, Gibbs r1p9, Nacua r2p13, Lamar Jackson
r2p17 — while `roster.keepers` held **two** entirely different players. Same
mismatch in 2025.

**The draft is the record.** `GET /draft/{draft_id}/picks`, filter `is_keeper`.
It also gives the round and pick the keeper cost, which is the part that
decides whether keeping someone was a good idea.

**`roster.keepers` is a forward designation** for the next draft. Managers
carry entries in it mid-season, so it is plainly not inert outside the
pre-draft window — but nothing observed here establishes how it is consumed at
the next draft. Do not assert a mechanism for it.

`roster_set_keepers(keepers: String, ...)` takes a JSON ARRAY AS A STRING —
`"[\"11584\"]"` — while the roster hands it back as a real list. Passing a
list to the mutation is accepted and stores nothing.

## `league_transactions_by_player`

Authenticated, unlike most league reads — it returns `Unauthorized` without a
token rather than an empty list.

**It spans seasons.** Sleeper follows the league's `previous_league_id` chain,
so a keeper league returns draft, waiver and trade history going back years.
Asking the 2026 league about a player returned moves from 2024.

**It returns every transaction that TOUCHED the player, in either direction.**
A row can read `adds: {"10226": 5}, drops: {"9754": 5}` — that is a claim on
10226, and 9754 is only the corresponding cut. The same row means "acquired"
in one player's history and "released" in another's, so rows must be classified
relative to the player asked about. Reading them all as acquisitions produces a
history in which a player was signed six times and never released.

**A trade puts the player in BOTH `adds` and `drops`**, moving him between the
two rosters in `roster_ids`. That is what distinguishes a trade from an add
that happened to cut somebody.

**The season rollover is one transaction typed `draft_pick`** with `adds: null`
and a dozen `drops` — the previous year's roster cleared before the new draft.
Rendered as an ordinary cut it becomes "dropped him for <three arbitrary
team-mates>" under the heading "draft": wrong about the reason, wrong about the
other names, and incoherent about the type. Team defences appear in these lists
keyed by team abbreviation (`"SF"`) rather than a numeric id.

Timestamps in `created` are epoch **milliseconds**. Read as seconds every
transaction lands in 1970.

## Stats: `weekly_stats`, `season_stats`, `get_player_stats`

Real production data — snaps, targets, carries, red-zone looks — lives here and
nowhere else. Three things about it are undocumented and all three bite.

**`category` is `"stat"`, singular.** Not `"stats"`, despite the query name.

**`order_by` is `String!` — required.** So is `category`. The obvious minimal
query, asking for one week of one sport, fails with
`Expected type "String!", found null` rather than anything that suggests which
argument to add. `order_by:"pts_half_ppr"` works.

**`season_stats` gives whole-season totals in one call**, with `gp` (games
played) and `week: null` on every row. One request replaces eighteen, and `gp`
is what makes a per-game rate possible — season totals reward availability as
much as quality, so a player who missed five games ranks below a worse one who
did not.

**A POSITION FILTER STRIPS THE TEAM ROWS.** Measured on one season:
`positions:["WR"]` returns 1,364 rows and **zero** `TEAM_` entries, against
8,248 rows and 32 team rows unfiltered. Since the team rows are the
denominators, filtering at the query and then computing shares divides a
receiver's targets by the WR-only total and inflates every share. Fetch
unfiltered and filter locally.

**Each week includes a synthetic per-team row.** Its `player_id` is
`TEAM_<abbr>` — `TEAM_LAR` — and its stats are the entire offence's: 48 targets,
39 carries. It is not a player.

That last one is the dangerous one, because it fails quietly in both
directions. Summing every row to compute a team total counts the offence
twice, halving every share; a receiver with 16 targets then reports a 9%
opportunity share, which is wrong to anyone who watches football and is
invisible to any assertion that shares lie between 0 and 1. Leaving the row in
a player list produces a phantom player who out-targets everyone.

Prefer the team row AS the denominator rather than merely excluding it. It is
Sleeper's own total, so shares stay correct even when the query is filtered to
one position — whereas summing players only works if every position came back,
and a `positions:"WR"` query that divides by its own sum yields shares above
1.0.

One more: a player's team in the stats row is where he played THAT WEEK. The
player dictionary holds where he plays today. For any past-season query they
differ, and the dictionary is the wrong source.

## Things that vary by league and must be read, not assumed

| Field | Why it matters |
|---|---|
| `roster_positions` | starting slots **in order**; `set_lineup` is positional, so a wrong assumption silently starts players in wrong slots. Filter out `BN`/`IR`/`TAXI`. |
| `scoring_settings` | the league's actual rulebook. `pts_ppr` and `pts_std` are presets and are wrong for anything else. |
| `waiver_type` | 0 rolling · 1 reverse standings · 2 FAAB. A bid is meaningless outside FAAB. |
| `trade_review_days` | **0 means an accepted trade executes immediately** — no vote, no veto window. |
| `reserve_slots`, `reserve_allow_*` | which designations may occupy IR. |

## The real-money surface

Roughly 69 of the ~240 queries belong to Sleeper's betting business:
`parlay`, `my_balances`, `my_payment_methods`, `tax_forms`,
`check_responsible_gaming_limits`, `download_cftc_monthly_statement` and
similar.

This server exposes none of them and refuses them by name — see
`sleeper_mcp/boundaries.py`. If you fork this, please keep that boundary:
event contracts are financial instruments, and a fantasy tool has no business
reaching into someone's balances or payment methods.

## Useful endpoints for onboarding

All public, no token:

```
GET /v1/user/<username>                        -> user_id
GET /v1/user/<user_id>/leagues/nfl/<season>    -> leagues
GET /v1/league/<league_id>/rosters             -> match owner_id for roster_id
GET /v1/state/nfl                              -> current week and season
```
