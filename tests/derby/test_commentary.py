"""Tests for derby.commentary — LLM and template commentary generation."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from derby.commentary import (
    SYSTEM_PROMPT,
    _build_prompt,
    build_standings_chart,
    build_template_commentary,
    generate_commentary,
)
from derby.logic import RaceResult, SegmentResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_result(num_segments: int = 2) -> RaceResult:
    """Build a minimal RaceResult for testing."""
    names = {1: "Thunderhoof", 2: "Shadowmane", 3: "Blazerunner"}
    segments = []
    for i in range(num_segments):
        segments.append(
            SegmentResult(
                position=i + 1,
                segment_type="straight" if i % 2 == 0 else "corner",
                segment_description=f"Segment {i + 1} desc",
                standings=[
                    (1, 20.0 + i, 40.0 + i),
                    (2, 18.0 + i, 36.0 + i),
                    (3, 15.0 + i, 30.0 + i),
                ],
                events=["Thunderhoof surges forward!"] if i == 0 else [],
            )
        )
    return RaceResult(
        placements=[1, 2, 3],
        segments=segments,
        racer_names=names,
        map_name="Test Track",
    )


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


class TestBuildPrompt:
    def test_contains_track_name(self):
        result = _make_result()
        prompt = _build_prompt(result)
        assert "Test Track" in prompt

    def test_contains_racer_names(self):
        result = _make_result()
        prompt = _build_prompt(result)
        assert "Thunderhoof" in prompt
        assert "Shadowmane" in prompt
        assert "Blazerunner" in prompt

    def test_contains_segment_info(self):
        result = _make_result()
        prompt = _build_prompt(result)
        assert "Segment 1 desc" in prompt
        assert "straight" in prompt
        assert "corner" in prompt

    def test_contains_events(self):
        result = _make_result()
        prompt = _build_prompt(result)
        assert "Thunderhoof surges forward!" in prompt

    def test_contains_final_placements(self):
        result = _make_result()
        prompt = _build_prompt(result)
        assert "1. Thunderhoof" in prompt
        assert "2. Shadowmane" in prompt
        assert "3. Blazerunner" in prompt

    def test_contains_scores(self):
        result = _make_result()
        prompt = _build_prompt(result)
        assert "pts)" in prompt


# ---------------------------------------------------------------------------
# Template fallback
# ---------------------------------------------------------------------------


class TestTemplateCommentary:
    def test_returns_list(self):
        result = _make_result()
        log = build_template_commentary(result)
        assert isinstance(log, list)
        assert len(log) > 0

    def test_includes_segment_headers(self):
        result = _make_result()
        log = build_template_commentary(result)
        assert any("Segment 1 desc" in line for line in log)

    def test_includes_standings(self):
        result = _make_result()
        log = build_template_commentary(result)
        assert any("Standings:" in line for line in log)
        assert any("Thunderhoof" in line for line in log)

    def test_includes_events(self):
        result = _make_result()
        log = build_template_commentary(result)
        assert any("surges forward" in line for line in log)

    def test_empty_segments_returns_empty(self):
        result = RaceResult(
            placements=[1, 2],
            segments=[],
            racer_names={1: "A", 2: "B"},
            map_name="Empty",
        )
        log = build_template_commentary(result)
        assert log == []


# ---------------------------------------------------------------------------
# LLM generation
# ---------------------------------------------------------------------------


class TestGenerateCommentary:
    @pytest.mark.asyncio
    async def test_returns_none_without_segments(self):
        """No segments means no LLM call needed."""
        result = RaceResult(
            placements=[1],
            segments=[],
            racer_names={1: "A"},
            map_name="Empty",
        )
        out = await generate_commentary(result)
        assert out is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_api_key(self):
        """Missing API key should gracefully return None."""
        import derby.commentary as mod

        mod._client = None  # reset cached client
        with patch.dict("os.environ", {}, clear=False):
            with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}):
                out = await generate_commentary(_make_result())
        mod._client = None  # cleanup
        assert out is None

    @pytest.mark.asyncio
    async def test_returns_paragraphs_on_success(self):
        """Successful API call returns split paragraphs."""
        fake_response = MagicMock()
        fake_response.content = [
            MagicMock(text="First segment action.\n\nSecond segment drama.\n\nThe winner!")
        ]
        fake_client = MagicMock()
        fake_client.messages.create.return_value = fake_response

        import derby.commentary as mod

        mod._client = fake_client

        result = _make_result()
        out = await generate_commentary(result)

        assert out is not None
        assert len(out) == 3
        assert "First segment action." in out[0]
        assert "The winner!" in out[2]

        # Verify the API was called with correct model and system prompt
        call_kwargs = fake_client.messages.create.call_args
        assert call_kwargs.kwargs["model"] == mod.MODEL
        assert call_kwargs.kwargs["system"] == SYSTEM_PROMPT

        mod._client = None  # cleanup

    @pytest.mark.asyncio
    async def test_returns_none_on_api_error(self):
        """API errors should be caught and return None."""
        fake_client = MagicMock()
        fake_client.messages.create.side_effect = Exception("API down")

        import derby.commentary as mod

        mod._client = fake_client

        out = await generate_commentary(_make_result())
        assert out is None

        mod._client = None  # cleanup

    @pytest.mark.asyncio
    async def test_returns_none_on_empty_response(self):
        """Empty LLM response should return None."""
        fake_response = MagicMock()
        fake_response.content = [MagicMock(text="")]
        fake_client = MagicMock()
        fake_client.messages.create.return_value = fake_response

        import derby.commentary as mod

        mod._client = fake_client

        out = await generate_commentary(_make_result())
        assert out is None

        mod._client = None  # cleanup

    @pytest.mark.asyncio
    async def test_logs_warning_when_llm_hits_max_tokens(self, caplog):
        """If the LLM stops because of max_tokens, log a warning so prod
        can tell when the final paragraph is likely clipped."""
        fake_response = MagicMock()
        fake_response.content = [MagicMock(text="Good paragraph.\n\nAnother one.\n\nFinal, cut off mid-se")]
        fake_response.stop_reason = "max_tokens"
        fake_client = MagicMock()
        fake_client.messages.create.return_value = fake_response

        import derby.commentary as mod
        mod._client = fake_client

        with caplog.at_level("WARNING", logger="discord_bot"):
            out = await generate_commentary(_make_result())
            assert out is not None  # still returns what it got
            assert any(
                "max_tokens" in rec.message.lower() for rec in caplog.records
            )
        mod._client = None  # cleanup

    @pytest.mark.asyncio
    async def test_no_warning_when_llm_stops_naturally(self, caplog):
        """Normal end_turn stop_reason should NOT trigger the warning."""
        fake_response = MagicMock()
        fake_response.content = [MagicMock(text="P1.\n\nP2.\n\nFinale!")]
        fake_response.stop_reason = "end_turn"
        fake_client = MagicMock()
        fake_client.messages.create.return_value = fake_response

        import derby.commentary as mod
        mod._client = fake_client

        with caplog.at_level("WARNING", logger="discord_bot"):
            out = await generate_commentary(_make_result())
            assert out is not None
            assert not any(
                "max_tokens" in rec.message.lower() for rec in caplog.records
            )
        mod._client = None  # cleanup


# ---------------------------------------------------------------------------
# Live standings bar chart
# ---------------------------------------------------------------------------


class TestBuildStandingsChart:
    """Unit tests for the live per-segment bar chart helper."""

    def _standings(self, cums: list[float]) -> list[tuple[int, float, float]]:
        """Build a standings list from a list of cumulative scores.

        The racer_id is just the index, seg_score is unused (set to 0).
        Returned already sorted desc by cumulative, as simulate_race
        produces.
        """
        return [
            (i + 1, 0.0, c)
            for i, c in enumerate(cums)
        ]

    def _names(self, count: int) -> dict[int, str]:
        pool = ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot"]
        return {i + 1: pool[i] for i in range(count)}

    def _colors(self, count: int) -> dict[int, str]:
        palette = ["\U0001f7e5", "\U0001f7e6", "\U0001f7e9",
                   "\U0001f7e8", "\U0001f7ea", "\U0001f7e7"]
        return {i + 1: palette[i] for i in range(count)}

    def test_returns_code_block(self):
        """Result is a ``` ... ``` wrapped string."""
        chart = build_standings_chart(
            self._standings([100.0, 80.0, 60.0]),
            self._names(3), self._colors(3),
        )
        assert chart.startswith("```")
        assert chart.rstrip().endswith("```")

    def test_leader_gets_full_bar_last_gets_empty(self):
        chart = build_standings_chart(
            self._standings([100.0, 80.0, 60.0, 40.0, 20.0]),
            self._names(5), self._colors(5),
            bar_width=10,
        )
        lines = chart.strip("`\n").split("\n")
        # Leader (id=1, Alpha) = 100 = all 10 filled
        assert "\u2588" * 10 in lines[0]
        # Last (id=5, Echo) = 20 = 0 filled, all empty
        assert "\u2591" * 10 in lines[-1]
        assert "\u2588" not in lines[-1]

    def test_midpack_bar_proportional_to_gap(self):
        """Bar length = round((cum - last) / spread * width)."""
        # cums: 100, 85, 70, 50, 30 → spread 70
        # relatives: 1.0, 0.786, 0.571, 0.286, 0.0
        # filled @ width=10: 10, 8, 6, 3, 0
        chart = build_standings_chart(
            self._standings([100.0, 85.0, 70.0, 50.0, 30.0]),
            self._names(5), self._colors(5),
            bar_width=10,
        )
        lines = chart.strip("`\n").split("\n")
        # Count █ in each line
        filled_counts = [line.count("\u2588") for line in lines]
        assert filled_counts == [10, 8, 6, 3, 0]

    def test_all_tied_gives_everyone_full_bar(self):
        """Edge case: spread=0 shouldn't divide-by-zero or make a mess."""
        chart = build_standings_chart(
            self._standings([50.0, 50.0, 50.0]),
            self._names(3), self._colors(3),
            bar_width=10,
        )
        lines = chart.strip("`\n").split("\n")
        # Everyone gets a full bar
        for line in lines:
            assert line.count("\u2588") == 10

    def test_includes_color_emoji_before_name(self):
        chart = build_standings_chart(
            self._standings([100.0, 50.0]),
            self._names(2), self._colors(2),
        )
        lines = chart.strip("`\n").split("\n")
        # "🟥 Alpha ..." — emoji precedes name
        assert lines[0].startswith("\U0001f7e5 Alpha")
        assert lines[1].startswith("\U0001f7e6 Bravo")

    def test_names_padded_for_alignment(self):
        """Short and long names in the same chart should align by column."""
        names = {1: "Boneshaker", 2: "Zip"}
        chart = build_standings_chart(
            self._standings([100.0, 50.0]),
            names, self._colors(2),
        )
        lines = chart.strip("`\n").split("\n")
        # Both name columns should be padded to "Boneshaker" (10 chars).
        # The bar starts at the same column in both lines.
        bar_pos_0 = lines[0].find("\u2588")
        bar_pos_1 = lines[1].find("\u2591")
        assert bar_pos_0 == bar_pos_1

    def test_empty_standings_returns_empty_string(self):
        chart = build_standings_chart({}, {}, {})
        assert chart == ""

    def test_missing_color_falls_back_gracefully(self):
        """If color_map is missing an id, the row still renders (no emoji)."""
        chart = build_standings_chart(
            self._standings([100.0, 50.0]),
            self._names(2),
            {1: "\U0001f7e5"},  # only first racer has a color
        )
        lines = chart.strip("`\n").split("\n")
        assert "Alpha" in lines[0]
        assert "Bravo" in lines[1]
        # Second line should NOT start with a color emoji
        assert not lines[1].startswith("\U0001f7e5")

    def test_custom_bar_width(self):
        """The bar_width param controls how many cells render."""
        chart = build_standings_chart(
            self._standings([100.0, 0.0]),
            self._names(2), self._colors(2),
            bar_width=20,
        )
        lines = chart.strip("`\n").split("\n")
        assert lines[0].count("\u2588") == 20
        assert lines[1].count("\u2591") == 20
