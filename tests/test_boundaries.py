"""The real-money boundary must hold. These run offline."""

import pytest

from sleeper_mcp.boundaries import RefusedByPolicy, check

FANTASY = [
    '{roster(league_id:"1"){players}}',
    '{get_player_news(sport:"nfl",player_id:"1"){metadata}}',
    '{trending_players(sport:"nfl",sort:"add"){player_id}}',
    'mutation{update_matchup_leg(round:1,leg:1,league_id:"1",roster_id:1)}',
    "/league/123/rosters",
]

MONEY = [
    "{my_balances{amount}}",
    '{parlay(id:"1"){legs}}',
    "mutation{place_wager(amount:10)}",
    "{my_payment_methods{id}}",
    "{download_cftc_monthly_statement}",
    "{tax_forms{year}}",
    "{check_responsible_gaming_limits}",
    "/user/me/wallet",
]


@pytest.mark.parametrize("q", FANTASY)
def test_fantasy_allowed(q):
    check(q)          # must not raise


@pytest.mark.parametrize("q", MONEY)
def test_money_refused(q):
    with pytest.raises(RefusedByPolicy):
        check(q)


# --- the allow-list, aliases and paths ----------------------------------------

from sleeper_mcp.boundaries import operations  # noqa: E402


def test_operations_are_parsed_from_every_document_shape():
    assert operations('{roster(league_id:"1"){players}}') == ["roster"]
    assert operations("mutation($a:Int){update_matchup_leg(round:$a){x}}") == \
        ["update_matchup_leg"]
    assert operations("{ me { user_id } }") == ["me"]
    assert operations("{a: my_balances{x} b: roster{y}}") == ["my_balances", "roster"]
    assert operations("query Q { scores(week:1){game_id} messages(parent_id:\"1\"){text} }") == \
        ["scores", "messages"]
    assert operations('{stats_for_players_in_week(player_ids:["1","2"],category:"proj"){player_id}}') == \
        ["stats_for_players_in_week"]


def test_an_alias_does_not_dodge_the_guard():
    with pytest.raises(RefusedByPolicy):
        check("{x: my_balances{amount}}")


def test_the_event_contract_mutation_is_refused_though_it_says_nothing_about_bets():
    """The exact case the deny-list missed: order_contract has none of the
    deny words in it, and is not an operation this server issues."""
    with pytest.raises(RefusedByPolicy):
        check("mutation{order_contract(event_id:\"1\",side:\"yes\"){id}}")


def test_an_unknown_but_innocent_query_is_refused_with_a_hint():
    with pytest.raises(RefusedByPolicy, match="ALLOWED"):
        check("{league_scoreboard(league_id:\"1\"){x}}")


def test_a_money_word_inside_a_selection_does_not_matter_only_the_operation():
    """A player named Stripe, selected under a legitimate query, is fine."""
    check('{roster(league_id:"1"){players stripe_count}}')


def test_a_username_with_a_money_word_is_a_legal_rest_path():
    for name in ("BengalStripes", "alphabet_soup", "Balanced_Attack", "kyc_fan"):
        check(f"/user/{name}")
        check(f"/user/{name}/leagues/nfl/2026")


def test_a_percent_encoded_dodge_is_decoded_first():
    with pytest.raises(RefusedByPolicy):
        check("/user/me/w%61llet")


def test_only_known_rest_routes_pass():
    check("/players/nfl/trending/add?lookback_hours=24&limit=25")
    check("/scores/nfl/regular/2026/2")
    with pytest.raises(RefusedByPolicy):
        check("/user/me/transactions")
