import datetime

import pytest

from dsw import ansi, render, state
from dsw.ansi import _visible_len
from dsw.render import (
    _bar,
    _build_frame,
    _fmt_age,
    _fmt_bytes,
    _parse_mem_usage,
    _sparkline,
    _to_bytes,
)
from dsw.state import Row

EXPECTED_HISTORY_SPARKLINE_WIDTH = 28
EXPECTED_DETAIL_LABEL_WIDTH = 15
EXPECTED_DETAIL_METRIC_VALUE_WIDTH = 8


class CountingFilter(str):
    def __new__(cls, value: str):
        obj = str.__new__(cls, value)
        obj.lower_calls = 0
        return obj

    def lower(self):
        self.lower_calls += 1
        return super().lower()


def make_row(
    cid: str,
    name: str,
    cpu: float = 1.0,
    mem_raw: str = "1%",
    mem_usage: str = "1MiB / 1GiB",
) -> Row:
    return Row(
        id=cid,
        name=name,
        cpu_raw=f"{cpu:.0f}%",
        cpu_val=cpu,
        mem_usage=mem_usage,
        mem_raw=mem_raw,
        mem_val=float(mem_raw.rstrip("%")),
        net_io="0B / 0B",
        block_io="0B / 0B",
    )


def render_text(width: int = 100) -> str:
    return _build_frame(width)[0].decode()


def first_non_empty_line(text: str) -> str:
    return next(line for line in text.splitlines() if line)


# -- existing tests ----------------------------------------------------------

def test_filter_prompt_uses_narrow_cursor_at_filter_cursor() -> None:
    state.S.bar_mode = state.BAR_FULL
    state.S.filter_typing = True
    state.S.filter_str = "abc"
    state.S.filter_cursor = 2

    output = render_text()
    filter_line = next(line for line in output.splitlines() if "filter>" in line)

    assert "filter> ab|c" in filter_line
    assert "█" not in filter_line


def test_filter_cursor_is_visible_in_header_when_key_bar_is_hidden() -> None:
    state.S.bar_mode = state.BAR_OFF
    state.S.filter_typing = True
    state.S.filter_str = "abc"
    state.S.filter_cursor = 2

    output = render_text()
    header_line = first_non_empty_line(output)

    assert "filter: 'ab|c'" in header_line
    assert "filter>" not in output


def test_filter_cursor_in_hidden_bar_header_is_gray(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ansi, "_no_color", False)
    monkeypatch.setattr(ansi, "_truecolor", True)
    state.S.bar_mode = state.BAR_OFF
    state.S.filter_typing = True
    state.S.filter_str = "abc"
    state.S.filter_cursor = 2

    output = render_text()
    header_line = first_non_empty_line(output)

    assert "\x1b[38;2;135;142;154m|\x1b[0m" in header_line
    assert "\x1b[1m\x1b[38;2;232;180;32m'ab\x1b[0m" in header_line
    assert "\x1b[1m\x1b[38;2;232;180;32mc'\x1b[0m" in header_line


def test_empty_filter_cursor_is_visible_in_header_when_key_bar_is_hidden() -> None:
    state.S.bar_mode = state.BAR_OFF
    state.S.filter_typing = True

    output = render_text()
    header_line = first_non_empty_line(output)

    assert "filter: '|'" in header_line


def test_name_sort_descending_matches_indicator():
    state.S.rows = [
        make_row("a", "alpha"),
        make_row("b", "beta"),
    ]
    state.S.sort_key = "name"
    state.S.sort_asc = False

    output = render_text()

    assert "name \u2193" in output
    assert output.index("beta") < output.index("alpha")


def test_memory_total_percentage_uses_usage_and_limit_bytes():
    state.S.rows = [
        make_row("small", "small", mem_raw="50%", mem_usage="512MiB / 1GiB"),
        make_row("large", "large", mem_raw="50%", mem_usage="5GiB / 10GiB"),
    ]
    state.S.sort_key = "none"

    output = render_text()
    total_line = next(line for line in output.splitlines() if line.startswith("TOTAL"))

    assert "50.00%" in total_line
    assert "100.00%" not in total_line


# -- _bar boundary tests (F-5) -----------------------------------------------

def test_bar_zero_value_is_all_empty():
    result = _bar(0.0, 10)
    # No filled blocks expected; result should only contain track chars (or be blank).
    assert "█" not in result


def test_bar_full_value_has_no_index_error():
    # 100% should not raise IndexError and should fill the bar.
    result = _bar(100.0, 10)
    assert result  # non-empty


def test_bar_over_100_clamped():
    # Values above 100% must not cause an IndexError or overflow.
    result = _bar(150.0, 10)
    assert result


def test_bar_negative_clamped():
    result = _bar(-5.0, 10)
    assert "█" not in result


# -- _to_bytes unit tests (F-9 / correctness) --------------------------------

def test_to_bytes_all_units():
    cases = [
        ("1B",   1.0),
        ("1KB",  1e3),
        ("1MB",  1e6),
        ("1GB",  1e9),
        ("1KiB", 1024.0),
        ("1MiB", 1024.0 ** 2),
        ("1GiB", 1024.0 ** 3),
    ]
    for s, expected in cases:
        assert _to_bytes(s) == expected, f"_to_bytes({s!r}) expected {expected}"


def test_to_bytes_b_suffix_not_ambiguous_with_kb():
    # 'b' suffix must not match 'kb' - longest-unit-first ordering prevents that.
    assert _to_bytes("1KiB") == 1024.0
    assert _to_bytes("1B") == 1.0


def test_to_bytes_returns_none_for_malformed():
    assert _to_bytes("nope") is None
    assert _to_bytes("xGiB") is None
    assert _to_bytes("") is None


def test_to_bytes_rejects_non_finite():
    # A daemon reporting inf/nan must not poison the TOTAL memory column.
    assert _to_bytes("infMiB") is None
    assert _to_bytes("nanMiB") is None
    assert _to_bytes("-infGiB") is None


def test_to_bytes_case_insensitive():
    assert _to_bytes("512mib") == 512 * 1024 ** 2
    assert _to_bytes("2GIB") == 2 * 1024 ** 3


# -- _parse_mem_usage tests --------------------------------------------------

def test_parse_mem_usage_standard():
    result = _parse_mem_usage("128MiB / 1GiB")
    assert result is not None
    used, limit, limit_str = result
    assert used == 128 * 1024 ** 2
    assert limit == 1024 ** 3
    assert limit_str == "1GiB"


def test_parse_mem_usage_missing_slash_returns_none():
    assert _parse_mem_usage("128MiB") is None


def test_parse_mem_usage_malformed_used_returns_none():
    assert _parse_mem_usage("xMiB / 1GiB") is None


def test_parse_mem_usage_preserves_limit_text_after_first_slash():
    result = _parse_mem_usage("128MiB / custom/limit")

    assert result is not None
    used, limit, limit_str = result
    assert used == 128 * 1024 ** 2
    assert limit is None
    assert limit_str == "custom/limit"


# -- _fmt_bytes scientific-notation regression (F-502) -----------------------

def test_fmt_bytes_no_scientific_notation_in_1000_band():
    # '.3g' would switch to exponent form once the scaled value reaches 1000
    # (the [1000, 1024) window of each unit band), e.g. '1e+03MiB'.
    assert "e+" not in _fmt_bytes(1000 * 1024 ** 2)
    assert _fmt_bytes(1000 * 1024 ** 2) == "1000MiB"
    assert _fmt_bytes(1010 * 1024 ** 3) == "1010GiB"
    assert _fmt_bytes(1023 * 1024 ** 2) == "1023MiB"


def test_fmt_bytes_keeps_three_sig_figs_below_100():
    assert _fmt_bytes(1.5 * 1024 ** 3) == "1.5GiB"
    assert _fmt_bytes(50 * 1024 ** 2) == "50MiB"
    assert _fmt_bytes(512) == "512B"


# -- TiB/TB memory units regression (F-503) ----------------------------------

def test_to_bytes_tib_and_larger_units():
    # Docker (go-units) emits TiB/TB and beyond for large hosts.
    assert _to_bytes("1.5TiB") == 1.5 * 1024 ** 4
    assert _to_bytes("2TB") == 2e12
    assert _to_bytes("1PiB") == 1024 ** 5
    assert _to_bytes("1PB") == 1e15


def test_parse_mem_usage_tib_limit_is_not_none():
    result = _parse_mem_usage("500MiB / 1.953TiB")
    assert result is not None
    used, limit, limit_str = result
    assert used == 500 * 1024 ** 2
    assert limit == 1.953 * 1024 ** 4
    assert limit_str == "1.953TiB"


def test_memory_total_percentage_handles_tib_limit():
    # Before F-503 a TiB limit failed to parse, blanking the TOTAL MEM % cell
    # to "-" even though every per-container value parsed fine.
    state.S.rows = [
        make_row("alpha", "alpha", mem_raw="50%", mem_usage="1TiB / 2TiB"),
        make_row("bravo", "bravo", mem_raw="50%", mem_usage="0.5TiB / 2TiB"),
    ]
    state.S.sort_key = "none"

    output = render_text()
    total_line = next(line for line in output.splitlines() if line.startswith("TOTAL"))

    # used = 1.5TiB, limit = 4TiB -> 37.50% (blanked to "-" before the fix)
    assert "37.50%" in total_line


# -- visible_ids returned from _build_frame (F-4) ----------------------------

def test_build_frame_returns_visible_ids():
    state.S.rows = [
        make_row("aa", "alpha"),
        make_row("bb", "beta"),
    ]
    state.S.sort_key = "name"
    state.S.sort_asc = True  # ascending: alpha < beta

    _, _, visible_ids = _build_frame(100)

    assert visible_ids == ["aa", "bb"]


def test_build_frame_visible_ids_respects_filter():
    state.S.rows = [
        make_row("aa", "alpha"),
        make_row("bb", "beta"),
    ]
    state.S.sort_key = "none"
    state.S.filter_str = "bet"

    _, _, visible_ids = _build_frame(100)

    assert visible_ids == ["bb"]
    assert "aa" not in visible_ids


def test_build_frame_filter_matches_all_space_separated_terms():
    state.S.rows = [
        make_row("aa", "web-prod-api"),
        make_row("bb", "web-dev-api"),
        make_row("cc", "prod-worker"),
    ]
    state.S.sort_key = "none"
    state.S.filter_str = "web prod"

    _, _, visible_ids = _build_frame(100)

    assert visible_ids == ["aa"]


def test_build_frame_filter_pipe_matches_any_term():
    state.S.rows = [
        make_row("aa", "web-dev-api"),
        make_row("bb", "prod-worker"),
        make_row("cc", "cache"),
    ]
    state.S.sort_key = "none"
    state.S.filter_str = "web|prod"

    _, _, visible_ids = _build_frame(100)

    assert visible_ids == ["aa", "bb"]


def test_build_frame_filter_double_ampersand_matches_all_terms():
    state.S.rows = [
        make_row("aa", "web-prod-api"),
        make_row("bb", "web-dev-api"),
        make_row("cc", "prod-worker"),
    ]
    state.S.sort_key = "none"
    state.S.filter_str = "web&&prod"

    _, _, visible_ids = _build_frame(100)

    assert visible_ids == ["aa"]


def test_build_frame_filter_combines_or_groups_with_and_terms():
    state.S.rows = [
        make_row("aa", "web-prod-api"),
        make_row("bb", "web-dev-api"),
        make_row("cc", "cache-prod"),
        make_row("dd", "cache-dev"),
    ]
    state.S.sort_key = "none"
    state.S.filter_str = "web&&prod|cache prod"

    _, _, visible_ids = _build_frame(100)

    assert visible_ids == ["aa", "cc"]


def test_filter_value_is_lowered_once_per_frame():
    fstr = CountingFilter("web")
    state.S.filter_str = fstr
    state.S.rows = [
        make_row("aa", "web-1"),
        make_row("bb", "web-2"),
        make_row("cc", "db"),
    ]

    _build_frame(100)

    assert fstr.lower_calls == 1


def test_build_frame_shrinks_name_column_for_narrow_terminal() -> None:
    state.S.rows = [
        make_row("aa", "a-very-long-container-name"),
    ]

    output = render_text(50)
    lines = output.splitlines()

    assert _visible_len(lines[3]) <= 50
    assert _visible_len(lines[4]) <= 50
    assert _visible_len(lines[5]) <= 50


def test_highlight_respects_no_color() -> None:
    state.S.rows = [
        make_row("aa", "alpha"),
    ]
    state.S.highlight = True
    state.S.sel_id = "aa"

    output = render_text(100)

    assert "alpha" in output
    assert "\x1b[" not in output


def test_metric_cells_are_bounded_to_terminal_width() -> None:
    state.S.rows = [
        Row(
            id="long",
            name="longmetrics",
            cpu_raw="1234567890%",
            cpu_val=1234567890.0,
            mem_usage="1234567890GiB / 1234567890GiB",
            mem_raw="1234567890%",
            mem_val=1234567890.0,
            net_io="1234567890GB / 1234567890GB",
            block_io="1234567890GB / 1234567890GB",
        ),
    ]

    output = render_text(140)
    data_line = next(line for line in output.splitlines() if "longmetrics" in line)

    assert _visible_len(data_line) <= 140


def test_key_bar_uses_lowercase_enter_for_detail_action() -> None:
    state.S.bar_mode = state.BAR_FULL
    state.S.rows = [make_row("web123", "web")]

    output = render_text(100)

    assert " enter  details" in output
    assert " enter  detail " not in output
    assert " Enter  details" not in output


def test_build_frame_returns_visual_row_count_for_wrapped_lines() -> None:
    state.S.error = "x" * 45

    output, row_count, _ = _build_frame(20)

    logical_rows = output.decode().count("\n") + 1
    assert row_count > logical_rows


def test_detail_view_renders_selected_container_metrics() -> None:
    state.S.rows = [
        make_row(
            "web123",
            "web",
            cpu=42.0,
            mem_raw="25%",
            mem_usage="256MiB / 1GiB",
        ),
        make_row("db123", "db", cpu=2.0),
    ]
    state.S.sel_id = "web123"
    state.S.detail_view = True

    output, _, visible_ids = _build_frame(100)
    text = output.decode()

    assert visible_ids == ["web123", "db123"]
    assert "web details" in text
    assert "CPU" in text
    assert "42%" in text
    assert "Memory" in text
    assert "Memory         25%" in text
    assert "256MiB / 1GiB" not in text
    assert " esc  back" in text
    assert " Esc  back" not in text
    assert "db" not in text


def test_detail_view_uses_recent_history_for_sparklines() -> None:
    state.S.rows = [make_row("web123", "web", cpu=30.0, mem_raw="30%")]
    state.S.sel_id = "web123"
    state.S.detail_view = True
    state.S.history = {"web123": ((0.0, 5.0), (50.0, 25.0), (100.0, 75.0))}

    output = render_text(100)

    assert "CPU history" not in output
    assert "Memory history" not in output
    assert "0% 50% 100%" not in output


def test_detail_view_history_sparklines_use_configured_width() -> None:
    state.S.rows = [make_row("web123", "web", cpu=30.0, mem_raw="30%")]
    state.S.sel_id = "web123"
    state.S.detail_view = True
    state.S.history = {
        "web123": tuple((float(i), float(i)) for i in range(EXPECTED_HISTORY_SPARKLINE_WIDTH + 1)),
    }

    output = render_text(120)
    lines = output.splitlines()
    cpu_line = next(line for line in lines if line.startswith("CPU "))
    mem_line = next(line for line in lines if line.startswith("Memory "))

    assert _visible_len(cpu_line) == (
        EXPECTED_DETAIL_LABEL_WIDTH + EXPECTED_DETAIL_METRIC_VALUE_WIDTH + EXPECTED_HISTORY_SPARKLINE_WIDTH
    )
    assert _visible_len(mem_line) == (
        EXPECTED_DETAIL_LABEL_WIDTH + EXPECTED_DETAIL_METRIC_VALUE_WIDTH + EXPECTED_HISTORY_SPARKLINE_WIDTH
    )


def test_detail_view_keeps_metadata_lines_in_one_section() -> None:
    state.S.rows = [make_row("web123", "web", cpu=30.0, mem_raw="30%")]
    state.S.sel_id = "web123"
    state.S.detail_view = True

    output = render_text(100)
    lines = output.splitlines()
    created_idx = next(i for i, line in enumerate(lines) if "Created" in line)
    restarts_idx = next(i for i, line in enumerate(lines) if "Restarts" in line)
    pids_idx = next(i for i, line in enumerate(lines) if "PIDs" in line)

    assert restarts_idx == created_idx + 1
    assert pids_idx == restarts_idx + 1


def test_detail_view_groups_cpu_and_memory_io_sections() -> None:
    state.S.rows = [make_row("web123", "web", cpu=30.0, mem_raw="30%")]
    state.S.sel_id = "web123"
    state.S.detail_view = True

    output = render_text(100)
    lines = output.splitlines()
    pids_idx = next(i for i, line in enumerate(lines) if "PIDs" in line)
    cpu_idx = next(i for i, line in enumerate(lines) if line.startswith("CPU "))
    memory_idx = next(i for i, line in enumerate(lines) if line.startswith("Memory "))
    network_idx = next(i for i, line in enumerate(lines) if "Network I/O" in line)
    block_idx = next(i for i, line in enumerate(lines) if "Block I/O" in line)
    ports_idx = next(i for i, line in enumerate(lines) if "Ports" in line)

    assert lines[pids_idx + 1] == ""
    assert cpu_idx == pids_idx + 2
    assert memory_idx == cpu_idx + 1
    assert network_idx == memory_idx + 1
    assert block_idx == network_idx + 1
    assert lines[block_idx + 1] == ""
    assert ports_idx == block_idx + 2


def test_sparkline_has_fixed_width() -> None:
    output = _sparkline((0.0, 50.0, 100.0), 8)

    assert _visible_len(output) == 8


def test_sparkline_renders_latest_sample_on_right() -> None:
    output = _sparkline((0.0, 50.0, 100.0), 5)

    assert output == "  ▁▅█"


# -- top N flag --------------------------------------------------------------

def test_build_frame_top_n_limits_rows():
    state.S.rows = [
        make_row("a", "alpha", cpu=10.0),
        make_row("b", "beta",  cpu=20.0),
        make_row("c", "gamma", cpu=5.0),
    ]
    state.S.sort_key = "cpu"
    state.S.sort_asc = False
    state.S.top_n = 2

    output = render_text()

    assert "beta" in output
    assert "alpha" in output
    assert "gamma" not in output


# -- terminal-height clamping (F-405) ----------------------------------------

def test_build_frame_clamps_data_rows_to_terminal_height():
    state.S.rows = [make_row(f"c{i}", f"cont{i:02d}", cpu=float(i)) for i in range(30)]

    output, row_count, visible_ids = _build_frame(120, term_h=20)
    text = output.decode()

    # The frame never occupies more rows than the terminal height, so the
    # cursor-up repaint in draw_loop stays on-screen.
    assert row_count <= 20
    # Some rows are hidden, announced by a truncation indicator.
    assert "more (sort or filter to see)" in text
    # visible_ids reflect exactly the rendered rows, not the full set.
    assert 0 < len(visible_ids) < 30
    assert all(f"cont{int(cid[1:]):02d}" in text for cid in visible_ids)
    # The TOTAL row still sums every container, not just the visible ones.
    assert "TOTAL" in text


def test_build_frame_fits_when_chrome_exceeds_height():
    # One long row on a tiny terminal: chrome alone would overflow, so the
    # frame must still report no more rows than term_h.
    state.S.rows = [make_row(f"c{i}", f"container-{i:02d}", cpu=float(i)) for i in range(8)]

    _, row_count, _ = _build_frame(24, term_h=6)

    assert row_count <= 6


def test_build_frame_fits_at_narrow_width():
    state.S.rows = [make_row(f"c{i}", f"container-name-{i:02d}", cpu=float(i)) for i in range(12)]

    _, row_count, _ = _build_frame(18, term_h=10)

    assert row_count <= 10


def test_build_frame_clamps_no_container_state():
    state.S.rows = []

    _, row_count, _ = _build_frame(20, term_h=4)

    assert row_count <= 4


def test_build_frame_clamps_error_state():
    state.S.rows = []
    state.S.error = "docker daemon not reachable: " + "x" * 80

    _, row_count, _ = _build_frame(20, term_h=5)

    assert row_count <= 5


def test_build_frame_no_clamp_when_rows_fit_height():
    state.S.rows = [make_row(f"c{i}", f"cont{i}") for i in range(3)]

    _, _, visible_ids = _build_frame(120, term_h=40)

    assert len(visible_ids) == 3


def test_build_frame_height_unbounded_by_default():
    state.S.rows = [make_row(f"c{i}", f"cont{i}") for i in range(50)]

    _, _, visible_ids = _build_frame(120)

    assert len(visible_ids) == 50


# -- _fmt_age coverage with a frozen "now" -----------------------------------

_FROZEN_NOW = datetime.datetime(2026, 6, 11, 12, 0, 0, tzinfo=datetime.UTC)


class _FrozenDatetime(datetime.datetime):
    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return _FROZEN_NOW if tz is None else _FROZEN_NOW.astimezone(tz)


@pytest.fixture
def frozen_now(monkeypatch):
    monkeypatch.setattr(render.datetime, "datetime", _FrozenDatetime)


@pytest.mark.parametrize(
    "iso_str, expected",
    [
        # Buckets, measured against 2026-06-11 12:00:00Z.
        ("2026-06-11T12:00:00Z", "just now (2026-06-11)"),
        ("2026-06-11T11:30:00Z", "30m ago (2026-06-11)"),
        ("2026-06-11T07:00:00Z", "5h ago (2026-06-11)"),
        ("2026-06-10T12:00:00Z", "1 day ago (2026-06-10)"),
        ("2026-06-01T12:00:00Z", "10 days ago (2026-06-01)"),
        ("2026-03-13T12:00:00Z", "3mo ago (2026-03-13)"),
        ("2024-06-11T12:00:00Z", "2y ago (2024-06-11)"),
        # Nanosecond precision is trimmed to microseconds before parsing.
        ("2026-06-11T10:00:00.533897677Z", "1h ago (2026-06-11)"),
        # A naive timestamp (no offset) is assumed to be UTC.
        ("2026-06-10T12:00:00", "1 day ago (2026-06-10)"),
    ],
)
def test_fmt_age_buckets(frozen_now, iso_str, expected):
    assert _fmt_age(iso_str) == expected


def test_fmt_age_empty_string_returns_empty(frozen_now):
    assert _fmt_age("") == ""


def test_fmt_age_future_returns_input_unchanged(frozen_now):
    # A timestamp ahead of now yields a negative delta; the raw string is kept.
    assert _fmt_age("2027-01-01T00:00:00Z") == "2027-01-01T00:00:00Z"


def test_fmt_age_malformed_returns_input_unchanged(frozen_now):
    assert _fmt_age("not-a-date") == "not-a-date"


