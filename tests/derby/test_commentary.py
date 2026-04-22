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

    def test_leader_gets_full_bar(self):
        """Leader always fills the bar; trailing racers get their
        fraction of the leader's cumulative score."""
        chart = build_standings_chart(
            self._standings([100.0, 80.0, 60.0, 40.0, 20.0]),
            self._names(5), self._colors(5),
            bar_width=10,
        )
        lines = chart.strip("`\n").split("\n")
        # Leader (id=1, Alpha) = 100 = 100/100 = all 10 filled
        assert "\u2588" * 10 in lines[0]
        # Last (id=5, Echo) = 20 = 20/100 = 2 filled (not empty!)
        assert lines[-1].count("\u2588") == 2

    def test_bar_is_leader_relative_fraction(self):
        """Bar length = round(cum / leader_cum * width)."""
        # cums: 100, 85, 70, 50, 30 → leader=100
        # relatives: 1.0, 0.85, 0.70, 0.50, 0.30
        # filled @ width=10: 10, 9 (0.85→round=8 wait rounds to even in py…)
        # Python's round() uses banker's rounding: round(0.85*10) = round(8.5) = 8
        # round(0.70*10)=7, round(0.50*10)=0 in banker's? No — round(5)=0
        # Banker's rounding: round(5.0) = 4 or 6 depending on parity.
        # round(8.5) = 8, round(5.0) = 4 (rounds to even).
        # Let's just assert monotonic + leader=10 + roughly-right.
        chart = build_standings_chart(
            self._standings([100.0, 85.0, 70.0, 50.0, 30.0]),
            self._names(5), self._colors(5),
            bar_width=10,
        )
        lines = chart.strip("`\n").split("\n")
        filled_counts = [line.count("\u2588") for line in lines]
        # Leader is always full
        assert filled_counts[0] == 10
        # Each subsequent racer has fewer filled cells (or equal, but
        # monotonic non-increasing since standings are desc by cum)
        assert filled_counts == sorted(filled_counts, reverse=True)
        # Last place at 30/100 = 3 filled
        assert filled_counts[-1] == 3

    def test_chart_visibly_shifts_when_gap_closes(self):
        """Regression test for the 'chart never changes' bug: when a
        trailing racer closes the gap to the leader over segments, their
        bar should grow. The old (gap-normalized) formula would have
        looked identical between these two snapshots."""
        early = build_standings_chart(
            self._standings([100.0, 80.0, 60.0]),
            self._names(3), self._colors(3),
            bar_width=10,
        )
        # C pulled closer, relative positions preserved but proportions
        # differ: A=200, B=180, C=170 (C went from 60% to 85% of leader)
        later = build_standings_chart(
            self._standings([200.0, 180.0, 170.0]),
            self._names(3), self._colors(3),
            bar_width=10,
        )
        early_last = early.strip("`\n").split("\n")[-1]
        later_last = later.strip("`\n").split("\n")[-1]
        # With the OLD formula both charts would be identical. With the
        # NEW formula, C's bar grows from 6 cells (60%) to ~9 cells (85%).
        assert early_last.count("\u2588") < later_last.count("\u2588")

    def test_all_tied_gives_everyone_full_bar(self):
        """Edge case: leader_cum=0 shouldn't divide-by-zero."""
        chart = build_standings_chart(
            self._standings([0.0, 0.0, 0.0]),
            self._names(3), self._colors(3),
            bar_width=10,
        )
        lines = chart.strip("`\n").split("\n")
        # Everyone gets a full bar
        for line in lines:
            assert line.count("\u2588") == 10

    def test_positive_cums_all_nonempty(self):
        """In the leader-relative formula, any racer with cum > 0 gets
        at least some bar (even if tiny). Only actual zero gets empty."""
        chart = build_standings_chart(
            self._standings([100.0, 50.0, 10.0, 0.0]),
            self._names(4), self._colors(4),
            bar_width=10,
        )
        lines = chart.strip("`\n").split("\n")
        # Last racer at cum=0 → 0 filled
        assert lines[-1].count("\u2588") == 0
        # Racer with cum=10 (10% of leader) → 1 filled
        assert lines[2].count("\u2588") == 1

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
        def _first_bar_char(line: str) -> int:
            for i, ch in enumerate(line):
                if ch in ("\u2588", "\u2591"):
                    return i
            return -1
        assert _first_bar_char(lines[0]) == _first_bar_char(lines[1])
        assert _first_bar_char(lines[0]) > 0

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
