"""
render.py - frame builders and all rendering helpers.

_build_frame()      → stats view (bytes, line_count)
_build_help_frame() → help overlay (bytes, line_count)
"""

import datetime
import math

from . import state
from .ansi import (
    _COL_BG_KEY,
    _COL_DIM,
    _COL_GREEN,
    _COL_GREY,
    _COL_HL_BG,
    _COL_KEY_FG,
    _COL_ORANGE,
    _COL_RED,
    _COL_TRACK,
    _COL_WHITE,
    _COL_YELLOW,
    _RESET,
    _bg,
    _bg_prefix,
    _bold_c,
    _c,
    _on,
    _pad_left,
    _pad_right,
    _trunc,
    _visible_len,
)
from .state import (
    HISTORY_SPARKLINE_WIDTH,
    ContainerDetails,
    Row,
)

# Render-layout constants. Width thresholds (in columns) at which the block-IO,
# net-IO, and mem-usage columns become visible, plus the two heat-bar widths.
COL_BLOCK     = 135
COL_NET       = 108
COL_MEM_USAGE = 90

BAR_WIDE   = 12
BAR_NARROW = 6

# Left-to-right eighths blocks for the fractional cell of a heat bar.
_EIGHTHS = " ▏▎▍▌▋▊▉█"

_SEP = "  |  "
_DETAIL_LABEL_WIDTH = 15
_DETAIL_METRIC_VALUE_WIDTH = 8


# -- rendering helpers ----------------------------------------------------------

def _visual_row_count(output: str, term_w: int) -> int:
    """Count terminal rows occupied by output after wrapping."""
    width = max(1, term_w)
    total = 0
    for line in output.split("\n"):
        vlen = _visible_len(line)
        total += max(1, -(-vlen // width) if vlen else 1)
    return total


def _heat_color(v: float) -> str:
    if v >= 80:
        return _COL_RED
    if v >= 60:
        return _COL_ORANGE
    if v >= 40:
        return _COL_YELLOW
    return _COL_GREEN


def _bar(value: float, width: int) -> str:
    filled  = max(0.0, min(float(width), value / 100.0 * width))
    n_full  = int(filled)
    # Clamp to [0, 8] to guard against floating-point rounding at boundaries.
    frac    = _EIGHTHS[min(8, round((filled - n_full) * 8))]
    n_empty = max(0, width - n_full - (0 if frac in (" ", "") else 1))
    col     = _heat_color(value)

    pieces: list[str] = []
    if n_full:
        pieces.append(_c(col, "█" * n_full))
    if frac not in (" ", ""):
        pieces.append(_c(col, frac))
    if n_empty:
        pieces.append(_c(_COL_TRACK, "▏" * n_empty))
    return "".join(pieces)


def _pct(raw: str, val: float) -> str:
    width = 7
    pct = raw
    if _visible_len(pct) > width:
        compact = f"{val:.0f}%"
        pct = compact if _visible_len(compact) <= width else ">" + "9" * (width - 2) + "%"
    return _bold_c(_heat_color(val), _trunc(pct, width).rjust(width))


def _dim(s: str) -> str:
    return _c(_COL_GREY, s)


def _fit_left_plain(s: str, width: int) -> str:
    return _pad_right(_trunc(s, width), width)


def _fit_right_plain(s: str, width: int) -> str:
    return _pad_left(_trunc(s, width), width)


def _col_label(text: str, key: str, active_key: str, asc: bool = False) -> str:
    if key and active_key == key:
        arrow = _c(_COL_GREEN, " ↑" if asc else " ↓")
        return _bold_c(_COL_WHITE, text) + arrow
    return _c(_COL_DIM, text)


def _key_badge(k: str) -> str:
    return _on(_COL_BG_KEY, _COL_KEY_FG, f" {k} ")


_SPARK_BLOCKS = "▁▂▃▄▅▆▇█"


def _sparkline(values: tuple[float, ...], width: int) -> str:
    width = max(1, width)
    if not values:
        return _dim(" " * width)
    visible_values = values[-width:]
    pieces: list[str] = []
    if len(visible_values) < width:
        pieces.append(_dim(" " * (width - len(visible_values))))
    for value in visible_values:
        clamped = max(0.0, min(100.0, value))
        idx = min(len(_SPARK_BLOCKS) - 1, int(clamped / 100.0 * len(_SPARK_BLOCKS)))
        pieces.append(_c(_heat_color(clamped), _SPARK_BLOCKS[idx]))
    return "".join(pieces)


def _detail_value(value: str) -> str:
    return value if value else "-"


def _detail_line(label: str, value: str, width: int) -> str:
    label_text = _c(_COL_DIM, f"{label:<{_DETAIL_LABEL_WIDTH}}")
    value_text = _bold_c(_COL_WHITE, _trunc(_detail_value(value), max(1, width - _DETAIL_LABEL_WIDTH)))
    return label_text + value_text


def _metric_history_line(label: str, value: str, history: tuple[float, ...], spark_width: int) -> str:
    label_text = _c(_COL_DIM, f"{label:<{_DETAIL_LABEL_WIDTH}}")
    value_text = _bold_c(_COL_WHITE, f"{_trunc(_detail_value(value), _DETAIL_METRIC_VALUE_WIDTH):<{_DETAIL_METRIC_VALUE_WIDTH}}")
    return label_text + value_text + _sparkline(history, spark_width)


def _fmt_age(iso_str: str) -> str:
    """Format a Docker ISO timestamp as 'N days ago (YYYY-MM-DD)'.

    Docker uses nanosecond precision (e.g. '2026-05-20T14:55:08.533897677Z')
    which Python's fromisoformat does not accept, so we trim the fractional
    seconds to at most 6 digits before parsing.
    """
    if not iso_str:
        return ""
    try:
        s = iso_str
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        # Trim sub-microsecond precision: find the dot, then skip digits.
        dot = s.find(".")
        if dot != -1:
            end = dot + 1
            while end < len(s) and s[end].isdigit():
                end += 1
            if end - dot - 1 > 6:
                s = s[:dot + 7] + s[end:]
        dt = datetime.datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.UTC)
        now = datetime.datetime.now(datetime.UTC)
        delta = now - dt
        days = delta.days
        if days < 0:
            return iso_str
        if days == 0:
            hours = delta.seconds // 3600
            mins = delta.seconds // 60
            if hours > 0:
                age_str = f"{hours}h ago"
            elif mins > 0:
                age_str = f"{mins}m ago"
            else:
                age_str = "just now"
        elif days == 1:
            age_str = "1 day ago"
        elif days < 30:
            age_str = f"{days} days ago"
        elif days < 365:
            months = round(days / 30)
            age_str = f"{months}mo ago"
        else:
            years = days // 365
            age_str = f"{years}y ago"
        return f"{age_str} ({dt.strftime('%Y-%m-%d')})"
    except (ValueError, OverflowError):
        return iso_str


def _build_detail_frame(
    row: Row,
    details: ContainerDetails | None,
    history: tuple[tuple[float, float], ...],
    term_w: int,
    visible_ids: list[str],
) -> tuple[bytes, int, list[str]]:
    content_w = max(16, term_w - 2)
    cpu_history = tuple(point[0] for point in history) or (row.cpu_val,)
    mem_history = tuple(point[1] for point in history) or (row.mem_val,)
    spark_w = max(8, min(HISTORY_SPARKLINE_WIDTH, term_w - 16))

    title = _c(_COL_GREEN, "▶  ") + _bold_c(_COL_WHITE, f"{row.name} details")
    meta = details or ContainerDetails(id=row.id)
    metadata_lines = [
        _detail_line("ID", row.id, content_w),
        _detail_line("Image", meta.image, content_w),
        _detail_line("Status", meta.status, content_w),
        _detail_line("Created", _fmt_age(meta.created), content_w),
        _detail_line("Restarts", meta.restart_count, content_w),
        _detail_line("PIDs", row.pids, content_w),
    ]
    cpu_lines = [
        _metric_history_line("CPU", row.cpu_raw, cpu_history, spark_w),
    ]
    usage_lines = [
        _metric_history_line("Memory", row.mem_raw, mem_history, spark_w),
        _detail_line("Network I/O", row.net_io, content_w),
        _detail_line("Block I/O", row.block_io, content_w),
    ]
    network_lines = [
        _detail_line("Ports", meta.ports, content_w),
        _detail_line("Network", meta.network, content_w),
        _detail_line("IP", meta.ip_address, content_w),
        _detail_line("Mounts", meta.mounts, content_w),
    ]
    data_lines = [
        *metadata_lines,
        "",
        *cpu_lines,
        *usage_lines,
        "",
        *network_lines,
        "",
        _key_badge("esc") + _c(_COL_GREY, " back  ") + _key_badge("q") + _c(_COL_GREY, " quit"),
    ]

    out_lines = ["", title, "", *data_lines, ""]
    output = "\n".join(out_lines)
    return output.encode(), _visual_row_count(output, term_w), visible_ids


# -- memory parsing helpers (module-level for testability) -------------------

_MEM_UNITS: dict[str, float] = {
    "b": 1, "kb": 1e3, "mb": 1e6, "gb": 1e9, "tb": 1e12, "pb": 1e15,
    "kib": 1024, "mib": 1024**2, "gib": 1024**3,
    "tib": 1024**4, "pib": 1024**5,
}

# Pre-sorted longest-unit-first so that multi-character suffixes (e.g. 'gib')
# are matched before their single-character prefix ('b').  Computed once at
# import time to avoid repeated allocation inside _to_bytes.
_MEM_UNITS_SORTED: list[tuple[str, float]] = sorted(
    _MEM_UNITS.items(), key=lambda x: len(x[0]), reverse=True
)


def _to_bytes(tok: str) -> float | None:
    """Convert a memory string like '1.5GiB' to bytes."""
    tok = tok.strip()
    for unit, factor in _MEM_UNITS_SORTED:
        if tok.lower().endswith(unit):
            try:
                value = float(tok[:-len(unit)]) * factor
            except ValueError:
                return None
            return value if math.isfinite(value) else None
    return None


def _parse_mem_usage(s: str) -> tuple[float, float | None, str] | None:
    """Parse a Docker mem usage string like '123MiB / 4GiB'.

    Returns (used_bytes, limit_bytes, limit_string) or None on parse failure.
    """
    parts = s.split("/", 1)
    if len(parts) != 2:
        return None
    used = _to_bytes(parts[0])
    limit_str = parts[1].strip()
    limit = _to_bytes(limit_str)
    return (used, limit, limit_str) if used is not None else None


def _fmt_bytes(b: float) -> str:
    """Format a byte count into a human-readable string."""
    for unit, threshold in (("GiB", 1024**3), ("MiB", 1024**2), ("KiB", 1024)):
        if b >= threshold:
            v = b / threshold
            # Avoid '.3g' here: it switches to exponent form once the scaled
            # value reaches 1000 (the [1000, 1024) band of each unit). Print
            # values >= 100 as integers and keep 3 significant digits below.
            return f"{v:.0f}{unit}" if v >= 100 else f"{v:.3g}{unit}"
    return f"{b:.0f}B"


def _parse_filter_expr(filter_text: str) -> list[list[str]]:
    """Parse OR groups separated by |, with AND terms split by space or &&."""
    parsed: list[list[str]] = []
    for group in filter_text.lower().split("|"):
        terms = [term for chunk in group.split("&&") for term in chunk.split()]
        if terms:
            parsed.append(terms)
    return parsed


def _filter_matches(name: str, filter_groups: list[list[str]]) -> bool:
    haystack = name.lower()
    return any(all(term in haystack for term in terms) for terms in filter_groups)


def _styled_filter_repr_with_cursor(filter_text: str, cursor: int) -> str:
    pos = max(0, min(cursor, len(filter_text)))
    rendered = repr(filter_text[:pos] + "\x00" + filter_text[pos:])
    before, after = rendered.split("\\x00", 1)
    return _bold_c(_COL_YELLOW, before) + _c(_COL_GREY, "|") + _bold_c(_COL_YELLOW, after)


# -- stats frame ------------------------------------------------------------

def _build_frame(term_w: int, term_h: int = 0) -> tuple[bytes, int, list[str]]:
    snapshot = state.snapshot()
    paused = snapshot.paused
    if paused:
        rows = list(snapshot.paused_rows)
        ts = snapshot.paused_ts
    else:
        rows = list(snapshot.rows)
        ts = snapshot.last_ts
    error = snapshot.error
    sk = snapshot.sort_key
    asc = snapshot.sort_asc
    fstr = snapshot.filter_str
    ftyping = snapshot.filter_typing
    fcursor = max(0, min(snapshot.filter_cursor, len(fstr)))
    top_n = snapshot.top_n
    hl = snapshot.highlight
    sel_id = snapshot.sel_id
    detail_view = snapshot.detail_view
    show_id = snapshot.show_id
    bar_full = snapshot.bar_mode == state.BAR_FULL
    bar_hint = snapshot.bar_mode == state.BAR_HINT

    show_block   = term_w >= COL_BLOCK
    show_net     = term_w >= COL_NET
    show_mem_usg = term_w >= COL_MEM_USAGE
    bar_w        = BAR_WIDE if show_net else BAR_NARROW

    if sk == "cpu":
        rows.sort(key=lambda r: r.cpu_val, reverse=not asc)
    elif sk == "mem":
        rows.sort(key=lambda r: r.mem_val, reverse=not asc)
    elif sk == "name":
        rows.sort(key=lambda r: r.name.casefold(), reverse=not asc)

    if fstr:
        filter_groups = _parse_filter_expr(fstr)
        rows = [r for r in rows if _filter_matches(r.name, filter_groups)]
    if top_n > 0:
        rows = rows[:top_n]

    # Collect the display order so the draw loop can publish it under the lock.
    visible_ids = [r.id for r in rows]
    sel = next((i for i, r in enumerate(rows) if r.id == sel_id), -1)
    if detail_view and sel >= 0:
        # Detail frame needs only the resolved rows/selection - return before
        # building the header and key bar, which it discards.
        detail_row = rows[sel]
        return _build_detail_frame(
            detail_row,
            snapshot.details.get(detail_row.id),
            snapshot.history.get(detail_row.id, ()),
            term_w,
            visible_ids,
        )

    n      = len(rows)
    # The TOTAL row sums every container; height clamping (below) only limits
    # the rendered data rows, so totals stay accurate over the full set.
    total_rows = rows
    ts_str = ts.strftime("%H:%M:%S") if ts else "-"
    _dir     = " ↑" if asc else " ↓"
    sk_label = {"cpu": f"cpu %{_dir}", "mem": f"mem %{_dir}", "name": f"name{_dir}", "none": "-"}.get(sk, "-")

    # -- header line ------------------------------------------------------------
    sep = _c(_COL_GREEN, _SEP)

    header_parts = [
        _c(_COL_GREEN, "▶  ") + _bold_c(_COL_WHITE, str(n)) + _c(_COL_GREY, f" container{'s' if n != 1 else ''}"),
        sep + _c(_COL_GREY, "sorted by ") + _bold_c(_COL_WHITE, sk_label),
        sep + (_bold_c(_COL_YELLOW, " PAUSED ") if paused
               else _c(_COL_GREY, "last update ") + _bold_c(_COL_WHITE, ts_str)),
    ]
    if fstr or (ftyping and not bar_full):
        header_filter = (
            _styled_filter_repr_with_cursor(fstr, fcursor)
            if ftyping and not bar_full
            else _bold_c(_COL_YELLOW, repr(fstr))
        )
        header_parts.append(sep + _c(_COL_GREY, "filter: ") + header_filter)
    if top_n > 0:
        header_parts.append(sep + _c(_COL_GREY, "top ") + _bold_c(_COL_WHITE, str(top_n)))

    header_line = "".join(header_parts)

    # In hint mode a one-line "b for shortcuts" sits in the key bar's slot;
    # pressing b cycles it to the full bar, then to nothing.
    subtitle_line = (
        _key_badge("b") + _c(_COL_GREY, " for shortcuts")
        if bar_hint and not ftyping
        else None
    )

    # -- key bar ----------------------------------------------------------------
    if ftyping:
        key_line = (
            _bold_c(_COL_GREEN, "filter> ")
            + _bold_c(_COL_WHITE, fstr[:fcursor])
            + _c(_COL_GREY, "|")
            + _bold_c(_COL_WHITE, fstr[fcursor:])
            + _c(_COL_GREY, "   Enter confirm   Esc cancel")
        )
    else:
        key_sections = [
            ("Sort:", [("n", "name"), ("c", "cpu"), ("m", "mem"), ("r", "reset")]),
            ("View:",  [("enter", "details"), ("/", "filter"), ("l", "highlight"), ("i", "id"), ("space", "pause"), ("b", "bar")]),
            ("",      [("h", "help"), ("q", "quit")]),
        ]
        ksep  = _c(_COL_GREEN, " |  ")
        key_parts: list[str] = []
        for s_idx, (section_name, pairs) in enumerate(key_sections):
            if section_name:
                key_parts.append(_bold_c(_COL_WHITE, section_name) + "  ")
            for k, desc in pairs:
                key_parts.append(_key_badge(k) + _c(_COL_GREY, f" {desc}  "))
            if s_idx < len(key_sections) - 1:
                key_parts.append(ksep)
        key_line = "".join(key_parts)

    # -- column definitions -----------------------------------------------------
    _name_natural  = max((_visible_len(r.name) for r in rows), default=16)
    _fixed_w       = (
        8 + bar_w
        + 8 + bar_w
        + (12 if show_id      else 0)
        + (22 if show_mem_usg else 0)
        + (18 if show_net     else 0)
        + (18 if show_block   else 0)
    )
    _ncols_total   = (1 + int(show_id) + 4
                      + int(show_mem_usg) + int(show_net) + int(show_block))
    _pad_total     = (_ncols_total - 1) * 2
    available_name_w = term_w - _fixed_w - _pad_total
    name_w         = max(4, min(_name_natural, available_name_w))

    cols: list[tuple[str, str, int, str]] = [
        ("CONTAINER", "name", name_w, "l"),
    ]
    if show_id:
        cols.append(("ID", "", 12, "l"))
    cols += [
        ("CPU %", "cpu", 8,     "r"),
        ("",      "",    bar_w, "l"),
        ("MEM %", "mem", 8,     "r"),
        ("",      "",    bar_w, "l"),
    ]
    if show_mem_usg:
        cols.append(("MEM USAGE", "", 22, "r"))
    if show_net:
        cols.append(("NET I/O",   "", 18, "r"))
    if show_block:
        cols.append(("BLOCK I/O", "", 18, "r"))

    PAD = "  "

    # -- header row -------------------------------------------------------------
    hcells: list[str] = []
    for label, skey, width, align in cols:
        rendered = _col_label(label, skey, sk, asc)
        cell = _pad_left(rendered, width) if align == "r" else _pad_right(rendered, width)
        hcells.append(cell)
    table_header = PAD.join(hcells)

    sep_line = _c(_COL_TRACK, PAD.join("─" * w for _, _, w, _ in cols))

    # -- totals row ----------------------------------------------------------
    # Built before the data rows so the height clamp below can measure the
    # totals block as part of the fixed chrome.
    totals_lines: list[str] = []
    if total_rows and not error:
        total_cpu = sum(r.cpu_val for r in total_rows)
        total_mem_pct: float | None = None

        total_mem_str = ""
        total_used = 0.0
        total_limit = 0.0
        total_limit_complete = True
        if show_mem_usg:
            limit_strs: set[str] = set()
            for r in total_rows:
                parsed = _parse_mem_usage(r.mem_usage)
                if parsed:
                    used, limit, limit_str = parsed
                    total_used += used
                    limit_strs.add(limit_str)
                    if limit is None:
                        total_limit_complete = False
                    else:
                        total_limit += limit
                else:
                    total_limit_complete = False
            if limit_strs:
                if len(limit_strs) == 1:
                    total_mem_str = f"{_fmt_bytes(total_used)} / {limit_strs.pop()}"
                else:
                    total_mem_str = f"{_fmt_bytes(total_used)} / mixed"
            if total_limit_complete and total_limit > 0:
                total_mem_pct = total_used / total_limit * 100.0

        totals_sep = _c(_COL_TRACK, "─" * sum(w for _, _, w, _ in cols)
                        + "──" * (len(cols) - 1))

        tcells: list[str] = [
            _c(_COL_DIM, _fit_left_plain("TOTAL", cols[0][2])),
        ]
        if show_id:
            tcells.append(_pad_right("", 12))
        mem_pct_for_bar = min(total_mem_pct, 100.0) if total_mem_pct is not None else None
        tcells += [
            _pad_left(_pct(f"{total_cpu:.2f}%", min(total_cpu, 100.0)), 8),
            _pad_right(_bar(min(total_cpu, 100.0), bar_w), bar_w),
            (
                _pad_left(_pct(f"{total_mem_pct:.2f}%", mem_pct_for_bar), 8)
                if total_mem_pct is not None and mem_pct_for_bar is not None
                else _pad_left(_dim("-"), 8)
            ),
            (
                _pad_right(_bar(mem_pct_for_bar, bar_w), bar_w)
                if mem_pct_for_bar is not None
                else _pad_right(_dim("-"), bar_w)
            ),
        ]
        if show_mem_usg:
            tcells.append(_dim(_fit_right_plain(total_mem_str if total_mem_str else "-", 22)))
        if show_net:
            tcells.append(_dim(_fit_right_plain("-", 18)))
        if show_block:
            tcells.append(_dim(_fit_right_plain("-", 18)))

        totals_lines = [totals_sep, PAD.join(tcells)]

    # -- data rows --------------------------------------------------------------
    data_lines: list[str] = []

    if error:
        data_lines.append(PAD + _c(_COL_RED, error))
    elif not rows:
        data_lines.append(PAD + _dim("no containers running"))
    else:
        for idx, row in enumerate(rows):
            cells: list[str] = [
                _c(_COL_WHITE, _fit_left_plain(row.name, cols[0][2])),
            ]
            if show_id:
                cells.append(_dim(_fit_left_plain(row.id, 12)))
            cells += [
                _pad_left(_pct(row.cpu_raw, row.cpu_val), 8),
                _pad_right(_bar(row.cpu_val, bar_w), bar_w),
                _pad_left(_pct(row.mem_raw, row.mem_val), 8),
                _pad_right(_bar(row.mem_val, bar_w), bar_w),
            ]
            if show_mem_usg:
                cells.append(_dim(_fit_right_plain(row.mem_usage, 22)))
            if show_net:
                cells.append(_dim(_fit_right_plain(row.net_io, 18)))
            if show_block:
                cells.append(_dim(_fit_right_plain(row.block_io, 18)))

            line = PAD.join(cells)
            if hl and (sel == idx):
                bg = _bg_prefix(_COL_HL_BG)
                if bg:
                    line = line.replace(_RESET, _RESET + bg)
                    plain_len = _visible_len(line)
                    line = _bg(_COL_HL_BG, line + " " * max(0, term_w - plain_len))
            data_lines.append(line)

    # -- assemble with a height budget (F-405) ----------------------------------
    # draw_loop repaints by moving the cursor up by the returned row count; a
    # frame taller than the terminal can never reach the top, so the cursor-up
    # is silently clamped and scrollback corrupts on every tick. When term_h is
    # known, keep the count within it: drop low-priority chrome in order (key
    # bar, then totals, then the table header) to free room for data, truncate
    # the data rows with a "... N more" indicator, and finally trim any
    # remaining overflow from the bottom as a hard guarantee. Visual rows are
    # counted (not logical lines) so wrapping on narrow terminals is included.
    header_sec = ["", header_line]
    thead_sec  = ["", table_header, sep_line]
    tail_content = key_line if bar_full else subtitle_line
    tail_sec   = ["", tail_content, ""] if tail_content else [""]

    def _vrows(lines: list[str]) -> int:
        return _visual_row_count("\n".join(lines), term_w) if lines else 0

    include_thead = include_totals = include_tail = True
    if term_h:
        def _chrome() -> int:
            return (
                _vrows(header_sec)
                + (_vrows(thead_sec) if include_thead else 0)
                + (_vrows(totals_lines) if include_totals else 0)
                + (_vrows(tail_sec) if include_tail else 0)
            )

        for _drop in ("tail", "totals", "thead"):
            if _chrome() + 1 <= term_h:
                break
            if _drop == "tail":
                include_tail = False
            elif _drop == "totals":
                include_totals = False
            else:
                include_thead = False

        if rows:
            data_budget = max(1, term_h - _chrome())
            if _vrows(data_lines) > data_budget:
                indicator_cost = _visual_row_count(
                    PAD + _dim(f"... {len(rows)} more (sort or filter to see)"), term_w
                )
                row_budget = max(1, data_budget - indicator_cost)
                kept: list[str] = []
                used = 0
                for line in data_lines:
                    cost = _visual_row_count(line, term_w)
                    if used + cost > row_budget and kept:
                        break
                    kept.append(line)
                    used += cost
                truncated = len(data_lines) - len(kept)
                rows = rows[:len(kept)]
                visible_ids = [r.id for r in rows]
                data_lines = [
                    *kept,
                    PAD + _dim(f"... {truncated} more (sort or filter to see)"),
                ]

    out_lines = [
        *header_sec,
        *(thead_sec if include_thead else []),
        *data_lines,
        *(totals_lines if include_totals else []),
        *(tail_sec if include_tail else []),
    ]
    output = "\n".join(out_lines)
    # Hard guarantee: drop trailing lines until the frame fits, in case wrapping
    # pushed the budgeted layout one row over on a very narrow terminal.
    if term_h:
        while _visual_row_count(output, term_w) > term_h and len(out_lines) > len(header_sec):
            out_lines.pop()
            output = "\n".join(out_lines)
    return output.encode(), _visual_row_count(output, term_w), visible_ids


# -- help frame -----------------------------------------------------------------

def _build_help_frame(term_w: int) -> tuple[bytes, int]:
    # Keys are shown with the same badge styling as the live key bar so the
    # help pane reads as the same vocabulary, not a separate cheat sheet.

    sections: list[tuple[str, list[tuple[str, str]]]] = [
        ("Sort", [
            ("c", "by CPU"),
            ("m", "by memory"),
            ("n", "by name"),
            ("r", "reset + clear filter"),
        ]),
        ("Select & filter", [
            ("↑↓",    "move selection"),
            ("l",     "toggle highlight"),
            ("enter", "open / close detail"),
            ("/",     "start filter"),
            ("esc",   "back / clear / deselect"),
        ]),
        ("Display", [
            ("space", "pause / resume"),
            ("i",     "toggle ID column"),
            ("b",     "cycle key bar (hint/full/off)"),
            ("h",     "close this help"),
            ("q",     "quit"),
        ]),
    ]

    def _make_panel(title: str, pairs: list[tuple[str, str]]) -> list[str]:
        key_w = max(_visible_len(_key_badge(k)) for k, _ in pairs)
        rows: list[str] = []
        for k, desc in pairs:
            badge = _pad_right(_key_badge(k), key_w)
            rows.append("  " + badge + "  " + _c(_COL_GREY, desc))
        header   = _c(_COL_GREEN, "▌ ") + _bold_c(_COL_WHITE, title)
        block_w  = max(_visible_len(header), *(_visible_len(r) for r in rows))
        block    = [_pad_right(header, block_w), " " * block_w]
        block   += [_pad_right(r, block_w) for r in rows]
        return block

    panels = [_make_panel(title, pairs) for title, pairs in sections]
    widths = [_visible_len(p[0]) for p in panels]

    # Lay panels out side by side when they fit; otherwise stack them so the
    # pane stays readable on narrow terminals.
    gap   = "    "
    zip_w = sum(widths) + len(gap) * (len(panels) - 1)
    if zip_w <= term_w:
        max_h  = max(len(p) for p in panels)
        padded = [p + [" " * w] * (max_h - len(p)) for p, w in zip(panels, widths, strict=True)]
        body_lines = [gap.join(row) for row in zip(*padded, strict=True)]
    else:
        body_lines = []
        for i, panel in enumerate(panels):
            body_lines += panel
            if i < len(panels) - 1:
                body_lines.append("")

    head_line = (
        _c(_COL_GREEN, "▶ ")
        + _c(_COL_GREY, "press ")
        + _key_badge("h")
        + _c(_COL_GREY, " to return")
    )

    footer_line = (
        _c(_COL_GREY, "Startup options: run ")
        + _bold_c(_COL_WHITE, "dsw --help")
        + _c(_COL_GREY, " for --sort, --filter, --runtime, --top, --highlight, --show-id, --no-color.")
    )

    out_lines = ["", head_line, "", *body_lines, "", footer_line, ""]
    output = "\n".join(out_lines)
    return output.encode(), _visual_row_count(output, term_w)
