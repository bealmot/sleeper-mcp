"""Draft tools and keepers against the fake league."""

import pytest

pytest.importorskip("httpx")

from tests import fake                                        # noqa: E402


@pytest.fixture
def sleeper(monkeypatch):
    fake.install(monkeypatch)
    return fake


@pytest.fixture
def writes(monkeypatch):
    fake.install(monkeypatch)
    fake.writes_on(monkeypatch)
    return fake


async def call(tool, **kw):
    return await getattr(tool, "fn", tool)(**kw)


async def test_draft_review_scores_under_league_settings_and_says_so(sleeper):
    from sleeper_mcp.drafts import draft_review
    out = await call(draft_review, season="2025")
    assert "under this league's scoring" in out
    # wr1: 39.0 league points (not the 60 preset)
    row = next(l for l in out.splitlines() if "Doe Catcher" in l)
    assert "39.0" in row


async def test_draft_review_warns_when_scoring_an_unfinished_season(sleeper):
    from sleeper_mcp.drafts import draft_review
    out = await call(draft_review, season="2026")
    assert "CAUTION" in out and "in progress" in out


async def test_draft_review_says_when_it_swapped_to_last_season(sleeper):
    from sleeper_mcp.drafts import draft_review
    out = await call(draft_review)
    assert "Showing 2025" in out


async def test_an_auction_draft_is_refused_by_review_and_shows_bids_on_the_board(sleeper, monkeypatch):
    from sleeper_mcp import drafts as d
    from sleeper_mcp.drafts import draft_board, draft_review
    real = d.rest

    async def auction(path):
        if path.startswith("/draft/") and not path.endswith("/picks"):
            return {"draft_id": "d1", "type": "auction", "status": "complete",
                    "season": "2026", "settings": {"rounds": 2}}
        if path.endswith("/picks"):
            return [{**p, "metadata": {"amount": "12"}} for p in fake.DRAFT_PICKS]
        return await real(path)
    monkeypatch.setattr(d, "rest", auction)
    assert "AUCTION" in await call(draft_review, season="2025")
    board = await call(draft_board)
    assert "(auction)" in board and "$12" in board


async def test_draft_board_refuses_a_round_that_does_not_exist(sleeper):
    from sleeper_mcp.drafts import draft_board
    out = await call(draft_board, round_=9)
    assert "No round 9" in out and "rounds 1-2" in out


async def test_traded_picks_marks_a_pick_whose_draft_has_run(sleeper, monkeypatch):
    from sleeper_mcp import drafts as d
    from sleeper_mcp.drafts import traded_picks
    real = d.gql

    async def used(q, variables=None, auth=False):
        out = await real(q, variables, auth)
        if "roster_draft_picks" in out:
            out["roster_draft_picks"].append({"season": "2026", "round": 3,
                                              "roster_id": 2, "owner_id": 1,
                                              "previous_owner_id": 2})
        return out
    monkeypatch.setattr(d, "gql", used)
    out = await call(traded_picks)
    lines = out.splitlines()
    assert any("2026" in l and "used" in l for l in lines)
    assert any("2027" in l and "used" not in l for l in lines)


async def test_keepers_is_gated_on_league_type_not_max_keepers(sleeper):
    """The guillotine clone carries max_keepers=1 and is NOT a keeper league."""
    from sleeper_mcp.keepers import keepers
    out = await call(keepers, league_id_="113")
    assert "not a keeper league" in out and "guillotine" in out
    assert "KEPT" in await call(keepers)


async def test_set_keepers_verifies_over_graphql(writes, monkeypatch):
    from sleeper_mcp import keepers as k
    from sleeper_mcp.keepers import set_keepers
    calls = []
    real = k.rest

    async def spy(path):
        calls.append(path)
        return await real(path)
    monkeypatch.setattr(k, "rest", spy)
    out = await call(set_keepers, player_names=["Doe Catcher"], confirm=True)
    assert out.startswith("VERIFIED") and "via league_rosters" in out
    assert calls == ["/league/111/rosters"]      # no REST read-back
    assert fake.STATE["keepers"][1] == ["wr1"]
