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

### A pending waiver claim is invisible to REST — at origin, not in cache

`/league/{id}/transactions/{week}` never carries a manager's own queued
waiver claim. A cache-busted fetch (`cf-cache-status: MISS`) still returns
none while `league_transactions_filtered(status_filters:["pending"])` returns
it, with `transaction_id`, `settings.waiver_bid`, `creator` and
`consenter_ids`. So a "pending" view needs the authenticated query, and
`cancel_waiver_claim` / `update_waiver_claim` cannot be fed from REST.

The bid is `settings.waiver_bid`. `waiver_budget` on a transaction is the list
of FAAB transfers inside a trade (`{sender, receiver, amount}` objects from
REST, `"sender,receiver,amount"` strings from GraphQL), and is null on a claim.

### The trade block and the waiver clock live in `league_players`

The REST roster has no trade-block field (`player_trade_block` and
`metadata.trade_block` do not exist). The PUBLIC `league_players(league_id)`
query returns one row per player the league has touched, and `settings`
carries:

| key | meaning |
|---|---|
| `otb` | the roster id that listed him on the trade block |
| `otb_added_at` | when, epoch **milliseconds** |
| `waiver_clears_at` | when he comes off waivers, epoch **SECONDS** |
| `last_added` | when he was last picked up, epoch seconds |

`add_league_player_trade_block` / `remove_league_player_trade_block` return a
`LeaguePlayer`, whose only fields are `metadata settings player_id league_id`
— selecting `roster_id` is a validation error before anything executes.

### Proposing a trade: the wire shape

`propose_trade(k_adds, v_adds, k_drops, v_drops, waiver_budget, draft_picks,
expires_at, reject_transaction_id)`. Every completed trade Sleeper records
lists every player in BOTH maps — `adds` keyed by the roster receiving him,
`drops` by the roster that held him — and a FAAB transfer as a
`"sender,receiver,amount"` string. This server sends the mutation in that
shape. It was derived from transaction records (REST and GraphQL agree) rather
than captured from the app's own request, so the first live proposal from the
tool is worth watching in `pending`.

### Roster ids are SLOTS, and owners change between seasons

`league_transactions_by_player` spans seasons, and each row's `roster_ids`
are slot numbers in the league the row belongs to (the row carries
`league_id`). Roster 7 was one manager in 2025 and another in 2026. Resolving
every row through the current league's owner table names the wrong manager;
resolve through the owner table of `row.league_id`.

### `matchup_legs` carries the score too

`MatchupLeg` has `points` (actual, null until played), `proj_points`,
`max_points` (Sleeper's own best-possible lineup for a finished week) and
`starters`. `matchup_legs_related_to_roster(league_id, roster_id,
start_round, end_round)` gives one roster's legs across weeks in one call
(authenticated). REST `/league/{id}/matchups/{week}` carries `points`,
`starters_points` and `players_points` unauthenticated.

### `league_rosters(league_id)` is the GraphQL roster read

Same fields as REST (`players`, `starters`, `reserve`, `taxi`, `keepers`,
`settings`) without the Cloudflare cache, so it is the read to verify a
`roster_update_reserve` or `roster_set_keepers` write with. The mutations'
own responses (`{roster_id reserve}`, `{roster_id keepers}`) are the
primary read-back either way.

### `messages` pages with `before`

`messages(parent_id!, before: Snowflake, order_by: String, show_hidden)`
returns 50 rows. `before` takes a message id and returns the older page;
`order_by` accepts only `"asc"` or `"desc"` (anything else is the HTTP 500
above). A message with no `text` may carry an `attachment` (a gif, an image).

### `settings.type` is the league format

0 redraft, 1 keeper, 2 dynasty, 3 guillotine (Sleeper's "chopped" leagues:
the lowest weekly score is eliminated, `playoff_week_start` is 0,
`disable_trades` is 1, and the roster of a chopped team is emptied).
`max_keepers` is set on redraft and guillotine leagues too, so it is not the
test for a keeper league. A clone of a league carries
`metadata.cloned_from`; the source carries `metadata.latest_cloned_to`.

`settings.last_scored_leg` is the last week Sleeper has written into the
record and `settings.leg` the current one — the league's own clock, which is
what decides "finished" for a league whose season is not the NFL's current
one.

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

**THE SCOREBOARD IS A SEPARATE ENDPOINT, and it is the only way to score a
pool.** `GET /scores/nfl/regular/<season>/<week>` returns one entry per game
whose `game_id` matches the pick'em ids exactly. The teams and scores are
inside `metadata` — `home_team`, `away_team`, `home_score`, `away_score` —
while `status` is top level, observed as `"complete"` and `"pre_game"`. Treat
anything but complete as undecided: a game in progress has a leader and not a
winner.

**`leg_id` is `"v1:regular:<week>"`**, not a snowflake. Get the list from
`get_pickem_legs(league_id, roster_id)`; both arguments are required, and it
returns EVERY week's leg, not the current one — pick by `leg_id`. Each leg
also carries `leg_scoring_result`, `{game_id: 1.0|0.0}` written by Sleeper as
each game finishes, which is the authoritative correct/incorrect for that
entry.

**The pool is discoverable without a share link.** `my_leagues` (authenticated,
no arguments needed) lists it with `sport: "pickem:nfl"` and
`roster_positions: null`; the public `rosters_by_user(user_id, sport:
"pickem:nfl", season_type: "regular", season)` returns the caller's entry
(`roster_id`) in it. Public REST `/league/<pool>/rosters` returns every entry
with `metadata.points_by_leg`, a JSON STRING of `{leg_id: points}` — Sleeper's
own scoring, the number the app shows.

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

`roster_set_keepers(keepers: ...)` was observed to persist a JSON ARRAY AS A
STRING — `"[\"11584\"]"` — while the roster hands it back as a real list, and
passing a bare list was observed to store nothing. Introspection declares the
argument `[String]`, so the server is coercing; the string form is what has
been seen to work.

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

## Trending, with a limit

`/v1/players/nfl/trending/{add|drop}?lookback_hours=24&limit=N` (public)
honours `limit` and returns `count` per player — how many leagues moved on
him. The GraphQL `trending_players(sport, sort)` takes no limit and returns
25.

## Useful endpoints for onboarding

All public, no token:

```
GET /v1/user/<username>                        -> user_id
GET /v1/user/<user_id>/leagues/nfl/<season>    -> leagues
GET /v1/league/<league_id>/rosters             -> match owner_id for roster_id
GET /v1/state/nfl                              -> current week and season
```
