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
