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
