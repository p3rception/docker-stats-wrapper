"""
ansi.py - ANSI escape helpers, color palette, and column layout utilities.
"""

import re
import unicodedata
from functools import cache, lru_cache

# -- regex ----------------------------------------------------------------------

# Includes the ESC prefix - used for measuring visible display width and for
# sanitizing daemon-provided fields. Strips whole string sequences (OSC, DCS,
# SOS, PM, APC) including their payload up to the terminator, so hostile
# container metadata cannot leak printable payload text; then CSI and the
# remaining two-byte Fe escapes.
_ANSI_STRIP_RE = re.compile(
    r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\|$)"   # OSC: ESC ] ... BEL | ST | end
    r"|\x1b[P^_X][^\x1b]*(?:\x1b\\|$)"       # DCS/SOS/PM/APC: ESC x ... ST | end
    r"|\x1b\[[0-?]*[ -/]*[@-~]"              # CSI
    r"|\x1b[@-Z\\-_]"                        # other two-byte Fe escapes
)

def _strip_ansi(s: str) -> str:
    return _ANSI_STRIP_RE.sub("", s)

# -- escape constants -----------------------------------------------------------

_RESET = "\x1b[0m"
_BOLD  = "\x1b[1m"

# Module-level flag: set True to suppress all color/style output.
# Configured at startup by __main__.py; avoids circular imports with state.
_no_color: bool = False

# Module-level flag: when False, colors are emitted as 256-color (38;5;N)
# sequences instead of 24-bit truecolor (38;2;r;g;b). Truecolor is only assumed
# when the terminal advertises it via COLORTERM; otherwise we fall back so that
# terminals without truecolor (e.g. macOS Terminal.app) render correct colors
# rather than mis-parsing the sequence. Configured at startup by __main__.py.
_truecolor: bool = True

# -- color palette --------------------------------------------------------------

_COL_GREEN  = "#27c77e"
_COL_YELLOW = "#e8b420"
_COL_ORANGE = "#f07d30"
_COL_RED    = "#f0485a"
_COL_GREY   = "#878e9a"
_COL_WHITE  = "#e2e8f0"
_COL_DIM    = "#718096"
_COL_TRACK  = "#1e2235"
_COL_HL_BG  = "#1e2a40"   # dim blue-grey background for selected row
_COL_BG_KEY = "#1e2235"
_COL_KEY_FG = "#a0aec0"

# -- ANSI primitives --------------------------------------------------------

@cache
def _hex_to_rgb(hex6: str) -> tuple[int, int, int]:
    """Parse a '#RRGGBB' hex color string into an (r, g, b) tuple.

    Cached: the palette is a handful of fixed constants, so each color is
    parsed once and the result reused for every styled cell.

    Raises ValueError on malformed input.
    """
    if len(hex6) != 7 or hex6[0] != "#":
        raise ValueError(f"invalid hex color: {hex6!r} (expected '#RRGGBB')")
    return int(hex6[1:3], 16), int(hex6[3:5], 16), int(hex6[5:7], 16)


# The 6 quantization levels of the xterm 6x6x6 color cube (codes 16-231).
_CUBE_LEVELS = (0, 95, 135, 175, 215, 255)


def _rgb_to_xterm256(r: int, g: int, b: int) -> int:
    """Map a 24-bit RGB color to the nearest xterm-256 palette index.

    Considers both the 6x6x6 color cube and the 24-step grayscale ramp and
    returns whichever is closer (squared-distance), so neutral greys map to the
    smoother ramp instead of the coarse cube.
    """
    def _cube_index(v: int) -> int:
        return min(range(6), key=lambda i: abs(_CUBE_LEVELS[i] - v))

    ri, gi, bi = _cube_index(r), _cube_index(g), _cube_index(b)
    cube_code = 16 + 36 * ri + 6 * gi + bi
    cube_rgb = (_CUBE_LEVELS[ri], _CUBE_LEVELS[gi], _CUBE_LEVELS[bi])

    # Grayscale ramp: code 232+i has value 8 + 10*i for i in 0..23.
    gray_i = max(0, min(23, round((round((r + g + b) / 3) - 8) / 10)))
    gray_val = 8 + 10 * gray_i
    gray_code = 232 + gray_i
    gray_rgb = (gray_val, gray_val, gray_val)

    def _dist(c: tuple[int, int, int]) -> int:
        return (c[0] - r) ** 2 + (c[1] - g) ** 2 + (c[2] - b) ** 2

    return cube_code if _dist(cube_rgb) <= _dist(gray_rgb) else gray_code


@cache
def _hex_to_256(hex6: str) -> int:
    """Cached '#RRGGBB' -> xterm-256 index (palette is a few fixed constants)."""
    return _rgb_to_xterm256(*_hex_to_rgb(hex6))


def _fg_sgr(hex6: str) -> str:
    """Foreground SGR parameters, truecolor or 256-color per `_truecolor`."""
    if _truecolor:
        r, g, b = _hex_to_rgb(hex6)
        return f"38;2;{r};{g};{b}"
    return f"38;5;{_hex_to_256(hex6)}"


def _bg_sgr(hex6: str) -> str:
    """Background SGR parameters, truecolor or 256-color per `_truecolor`."""
    if _truecolor:
        r, g, b = _hex_to_rgb(hex6)
        return f"48;2;{r};{g};{b}"
    return f"48;5;{_hex_to_256(hex6)}"


def _c(hex6: str, text: str) -> str:
    """Wrap text in a foreground colour escape."""
    if _no_color:
        return text
    return f"\x1b[{_fg_sgr(hex6)}m{text}{_RESET}"


def _bold_c(hex6: str, text: str) -> str:
    if _no_color:
        return text
    return f"{_BOLD}\x1b[{_fg_sgr(hex6)}m{text}{_RESET}"


def _bg_prefix(hex6: str) -> str:
    """Return a background escape prefix."""
    if _no_color:
        return ""
    return f"\x1b[{_bg_sgr(hex6)}m"


def _bg(hex6: str, text: str) -> str:
    """Wrap text in a background escape."""
    bg = _bg_prefix(hex6)
    if not bg:
        return text
    return f"{bg}{text}{_RESET}"


def _on(bg: str, fg: str, text: str) -> str:
    """Bold text with a background colour (for key badges)."""
    if _no_color:
        return text
    return (
        f"{_BOLD}"
        f"\x1b[{_bg_sgr(bg)}m"
        f"\x1b[{_fg_sgr(fg)}m"
        f"{text}{_RESET}"
    )

# -- column layout helpers ------------------------------------------------------

@lru_cache(maxsize=4096)
def _visible_len(s: str) -> int:
    """Return the printable length of a string (strip ANSI escapes)."""
    return sum(_char_width(ch) for ch in _ANSI_STRIP_RE.sub("", s))


def _char_width(ch: str) -> int:
    """Return the terminal cell width for a single printable character."""
    cp = ord(ch)
    if cp < 0x80:
        # ASCII fast path: control chars are width 0, everything else is width 1.
        return 0 if cp < 0x20 or cp == 0x7F else 1
    if unicodedata.combining(ch):
        return 0
    if unicodedata.category(ch)[0] == "C":
        return 0
    return 2 if unicodedata.east_asian_width(ch) in {"F", "W"} else 1


def _pad_right(s: str, width: int, fill: str = " ") -> str:
    """Right-pad s to display-width `width`, accounting for ANSI escapes."""
    return s + fill * max(0, width - _visible_len(s))


def _pad_left(s: str, width: int, fill: str = " ") -> str:
    """Left-pad (right-justify) s to display-width `width`."""
    return fill * max(0, width - _visible_len(s)) + s


def _trunc(s: str, width: int) -> str:
    """Truncate a plain (no ANSI) string to `width` chars."""
    if width <= 0:
        return ""
    out: list[str] = []
    used = 0
    for ch in s:
        ch_w = _char_width(ch)
        if used + ch_w > width:
            break
        out.append(ch)
        used += ch_w
    return "".join(out)
