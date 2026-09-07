import pytest

import dsw.ansi as ansi
from dsw.ansi import (
    _bg_prefix,
    _c,
    _hex_to_256,
    _on,
    _pad_right,
    _rgb_to_xterm256,
    _trunc,
    _visible_len,
)


def test_visible_len_counts_terminal_cell_width():
    assert _visible_len("\x1b[31m\u8868\x1b[0m") == 2
    assert _visible_len("e\u0301") == 1


def test_visible_len_caches_repeated_width_calculations() -> None:
    assert hasattr(_visible_len, "cache_info")
    assert hasattr(_visible_len, "cache_clear")

    _visible_len.cache_clear()  # type: ignore[attr-defined]

    assert _visible_len("container") == 9
    assert _visible_len("container") == 9

    assert _visible_len.cache_info().hits == 1  # type: ignore[attr-defined]


def test_padding_and_truncation_use_display_width():
    assert _pad_right("\u8868", 3) == "\u8868 "
    assert _trunc("\u8868abc", 2) == "\u8868"


# -- 256-color fallback (F-406) ----------------------------------------------

def test_rgb_to_xterm256_maps_primaries_to_cube_corners():
    assert _rgb_to_xterm256(0, 0, 0) == 16        # cube origin (black)
    assert _rgb_to_xterm256(255, 255, 255) == 231  # cube far corner (white)
    assert _rgb_to_xterm256(255, 0, 0) == 196      # pure red
    assert _rgb_to_xterm256(0, 255, 0) == 46       # pure green
    assert _rgb_to_xterm256(0, 0, 255) == 21       # pure blue


def test_rgb_to_xterm256_uses_grayscale_ramp_for_neutral_greys():
    # A true neutral grey is closer to the 24-step ramp than the coarse cube.
    code = _rgb_to_xterm256(128, 128, 128)
    assert 232 <= code <= 255


def test_rgb_to_xterm256_index_in_palette_range():
    for hex6 in (
        ansi._COL_GREEN, ansi._COL_RED, ansi._COL_YELLOW, ansi._COL_WHITE,
        ansi._COL_TRACK, ansi._COL_HL_BG, ansi._COL_KEY_FG,
    ):
        assert 16 <= _hex_to_256(hex6) <= 255


def test_truecolor_flag_selects_24bit_sequence(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ansi, "_no_color", False)
    monkeypatch.setattr(ansi, "_truecolor", True)
    out = _c("#27c77e", "x")
    assert "38;2;39;199;126" in out
    assert "38;5;" not in out


def test_non_truecolor_falls_back_to_256(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ansi, "_no_color", False)
    monkeypatch.setattr(ansi, "_truecolor", False)
    fg = _c("#27c77e", "x")
    assert f"38;5;{_hex_to_256('#27c77e')}m" in fg
    assert "38;2;" not in fg

    bg = _bg_prefix("#1e2a40")
    assert bg == f"\x1b[48;5;{_hex_to_256('#1e2a40')}m"

    badge = _on("#1e2235", "#a0aec0", " q ")
    assert "48;5;" in badge and "38;5;" in badge
    assert "2;" not in badge.replace("\x1b[", "")


def test_no_color_overrides_256_fallback(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ansi, "_no_color", True)
    monkeypatch.setattr(ansi, "_truecolor", False)
    assert _c("#27c77e", "x") == "x"
    assert _bg_prefix("#1e2a40") == ""
