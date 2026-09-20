"""Read tools, against the shapes that used to be wrong."""

import pytest

pytest.importorskip("httpx")

from tests import fake                                        # noqa: E402


@pytest.fixture
def sleeper(monkeypatch):
    fake.install(monkeypatch)
    return fake


async def call(tool, **kw):
    return await getattr(tool, "fn", tool)(**kw)


# --- roster -------------------------------------------------------------------

async def test_roster_names_the_manager_and_labels_projections(sleeper):
    from sleeper_mcp.reads import roster
    out = await call(roster)
    assert "Alder, roster 1" in out and "(projected)" in out


async def test_roster_scores_against_the_leagues_own_keys(sleeper):
    """qb1 = 250*0.04 + 2*4 + 12*0.25 = 21.0 under the fixture's scoring."""
    from sleeper_mcp.reads import roster
    out = await call(roster)
    assert "Ant Quarterback           21.00" in out


async def test_an_ir_player_is_listed_under_ir_not_the_bench(sleeper):
    from sleeper_mcp.reads import roster
    out = await call(roster)
    bench = out.split("BENCH")[1].split("IR")[0]
    assert "Moth Catcher" not in bench and "Moth Catcher" in out.split("\nIR")[1]


async def test_a_defence_prints_its_name_and_a_two_way_player_his_fantasy_position(sleeper):
    from sleeper_mcp.reads import roster
    out = await call(roster)
    assert "Aard Varks" in out and "WR    Kit Twoway" in out


async def test_a_past_week_shows_the_lineup_as_set_and_actual_points(sleeper):
    from sleeper_mcp.reads import roster
    out = await call(roster, week=3)
    assert "(actual)" in out and "Ant Quarterback           21.00" in out
    assert "total                     72.50" in out


# --- matchup -------------------------------------------------------------------

async def test_matchup_shows_actual_points_for_a_finished_week(sleeper):
    from sleeper_mcp.reads import matchup
    out = await call(matchup, week=3)
    assert "FINAL" in out and "Alder* 100.0 vs Birch 90.0" in out


async def test_matchup_labels_the_current_week_as_projected(sleeper):
    from sleeper_mcp.reads import matchup
    out = await call(matchup)
    assert "in progress" in out and "101.0 proj" in out


async def test_matchup_without_a_token_still_shows_points(sleeper, monkeypatch):
    fake.no_token(monkeypatch)
    from sleeper_mcp.reads import matchup
    out = await call(matchup, week=3)
    assert "need SLEEPER_TOKEN" in out and "100.0" in out


# --- standings ------------------------------------------------------------------

async def test_a_guillotine_league_shows_the_chop_not_a_record(sleeper):
    from sleeper_mcp.reads import standings
    out = await call(standings, league_id_="113")
    assert "GUILLOTINE" in out and "alive" in out and "W-L-T" not in out


# --- news and outlook ----------------------------------------------------------

async def test_player_news_prints_the_id_the_full_text_and_the_analysis(sleeper):
    from sleeper_mcp.reads import player_news
    out = await call(player_news, player_name="Doe Catcher")
    assert "id=wr1" in out and "analysis: It may matter." in out
    assert len([l for l in out.splitlines() if "He did a thing" in l][0]) > 400


async def test_player_news_works_for_a_defence_and_an_unsigned_player(sleeper):
    from sleeper_mcp.reads import player_news
    assert "Aard Varks" in await call(player_news, player_name="Varks")
    out = await call(player_news, player_name="Newt Catcher")
    assert "Newt Catcher — WR unsigned" in out


async def test_player_news_for_a_two_way_player_shows_his_fantasy_position(sleeper):
    from sleeper_mcp.reads import player_news
    assert "Kit Twoway — WR" in await call(player_news, player_name="Twoway")


# --- transactions / pending ----------------------------------------------------

async def test_transactions_renders_a_trade_by_what_each_side_gets(sleeper):
    from sleeper_mcp.reads import transactions
    out = await call(transactions)
    assert out.count("Doe Catcher") == 1 and out.count("Cat Runner") == 1
    assert "2027 round 6" in out and "$5 FAAB" in out and "bid $7" in out


async def test_pending_shows_your_own_waiver_claim_with_its_id_and_bid(sleeper):
    """The public feed never carries it; the authenticated source does."""
    from sleeper_mcp.reads import pending
    out = await call(pending)
    assert "id=t3" in out and "bid $12" in out and "Fox Catcher" in out
    assert "Nothing pending" not in out


async def test_pending_without_a_token_says_what_it_cannot_see(sleeper, monkeypatch):
    fake.no_token(monkeypatch)
    from sleeper_mcp.reads import pending
    out = await call(pending)
    assert "NOTE" in out and "private" in out and "t3" not in out


# --- chat --------------------------------------------------------------------------

async def test_chat_pages_back_and_renders_attachments(sleeper):
    from sleeper_mcp.reads import chat
    out = await call(chat, limit=10)
    assert "old trade talk" in out and "[gif]" in out and "3 of 3" in out


async def test_chat_search_reaches_older_pages(sleeper):
    from sleeper_mcp.reads import chat
    out = await call(chat, search="old")
    assert "1 of 1 message(s) matching 'old'" in out


# --- trending --------------------------------------------------------------------

async def test_trending_shows_counts_and_league_availability(sleeper):
    from sleeper_mcp.reads import trending
    out = await call(trending, limit=5)
    assert "900 leagues" in out and "ON WAIVERS" in out and "FREE" in out


# --- pick'em ----------------------------------------------------------------------

async def test_pickem_status_uses_the_weeks_leg_and_sleepers_own_result(sleeper):
    from sleeper_mcp.reads import pickem_status
    out = await call(pickem_status, week=4)
    assert "v1:regular:4" in out and "HIT" in out and "MISS" in out
    assert "1 correct, 1 wrong" in out


async def test_pickem_status_does_not_fall_back_to_another_weeks_leg(sleeper):
    from sleeper_mcp.reads import pickem_status
    out = await call(pickem_status, week=9)
    assert "No pick'em leg for week 9" in out


async def test_pickem_status_flags_missing_picks(sleeper):
    from sleeper_mcp.reads import pickem_status
    out = await call(pickem_status)
    assert "1 of 2 picks made" in out and "1 PICK(S) MISSING" in out


# --- history and search --------------------------------------------------------

async def test_player_history_attributes_a_previous_season_to_its_owner_then(sleeper):
    """Roster 1 belonged to Yew in 2025 and to Alder in 2026."""
    from sleeper_mcp.reads import player_history
    out = await call(player_history, player_name="Doe Catcher")
    assert "Yew drafted him" in out and "Alder drafted him" not in out


async def test_player_history_shows_what_came_back_in_a_trade(sleeper):
    from sleeper_mcp.reads import player_history
    out = await call(player_history, player_name="Doe Catcher")
    assert "Birch -> Alder for Cat Runner" in out


async def test_transaction_search_shows_bids_and_pending_ids(sleeper):
    from sleeper_mcp.reads import transaction_search
    out = await call(transaction_search)
    assert "bid $7" in out and "id=t3" in out and "cancelled" in out


async def test_transaction_search_rejects_an_unknown_filter(sleeper):
    from sleeper_mcp.reads import transaction_search
    out = await call(transaction_search, kind='trade"]){x')
    assert "Unknown filter" in out


async def test_a_bad_league_id_is_refused_before_any_query(sleeper):
    from sleeper_mcp.client import ConfigError
    from sleeper_mcp.reads import standings
    with pytest.raises(ConfigError):
        await call(standings, league_id_='1"){x')
