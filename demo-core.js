(function attachDemoCore(root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  root.DswDemoCore = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function buildDemoCore() {
  "use strict";

  const CYCLES = 60;
  const MEM_TOTAL_GIB = 7.654;
  const HISTORY_WIDTH = 28;
  const TICK_OFFSET = 26;

  const containers = [
    {
      name: "web-1",
      id: "e735e7f0adc5",
      pids: "9",
      net: [310, 120],
      block: [40, 12],
      cpu: (t) => 52 + 30 * Math.sin((2 * Math.PI * t) / 20),
      mem: (t) => 9 + 1.5 * Math.sin((2 * Math.PI * t) / 30),
      details: {
        image: "nginx:1.27-alpine",
        status: "running",
        created: "12 days ago (2026-05-31)",
        restarts: "0",
        ports: "0.0.0.0:8080->80/tcp",
        network: "frontend",
        ip: "172.21.0.12",
        mounts: "/srv/www->/usr/share/nginx/html",
      },
    },
    {
      name: "web-2",
      id: "9f2b1c3d4e5a",
      pids: "9",
      net: [180, 75],
      block: [25, 8],
      cpu: (t) => 30 + 14 * Math.sin((2 * Math.PI * t) / 15 + 1.3),
      mem: (t) => 7 + 1 * Math.sin((2 * Math.PI * t) / 20 + 2),
      details: {
        image: "nginx:1.27-alpine",
        status: "running",
        created: "12 days ago (2026-05-31)",
        restarts: "1",
        ports: "0.0.0.0:8081->80/tcp",
        network: "frontend",
        ip: "172.21.0.13",
        mounts: "/srv/www->/usr/share/nginx/html",
      },
    },
    {
      name: "api-gateway",
      id: "bae69eef0303",
      pids: "14",
      net: [240, 230],
      block: [10, 30],
      cpu: (t) => 38 + 22 * Math.sin((2 * Math.PI * t) / 12 + 2.1),
      mem: (t) => 11.5 + 0.8 * Math.sin((2 * Math.PI * t) / 30 + 1),
      details: {
        image: "ghcr.io/p3rception/synapse-api:demo",
        status: "running",
        created: "4 days ago (2026-06-08)",
        restarts: "0",
        ports: "0.0.0.0:3000->3000/tcp",
        network: "backend",
        ip: "172.22.0.8",
        mounts: "/var/run/docker.sock->/var/run/docker.sock",
      },
    },
    {
      name: "postgres",
      id: "cad83dbde4eb",
      pids: "21",
      net: [90, 140],
      block: [350, 480],
      cpu: (t) => 11 + 8 * Math.sin((2 * Math.PI * t) / 10 + 0.5),
      mem: (t) => 19 + 2 * Math.sin((2 * Math.PI * t) / 30),
      details: {
        image: "postgres:16-alpine",
        status: "running",
        created: "7 days ago (2026-06-05)",
        restarts: "0",
        ports: "127.0.0.1:5432->5432/tcp",
        network: "backend",
        ip: "172.22.0.9",
        mounts: "pgdata->/var/lib/postgresql/data",
      },
    },
    {
      name: "redis-cache",
      id: "7a8b9c0d1e2f",
      pids: "5",
      net: [150, 150],
      block: [5, 60],
      cpu: (t) => 4 + 3 * Math.sin((2 * Math.PI * t) / 6),
      mem: (t) => 3 + 0.4 * Math.sin((2 * Math.PI * t) / 15),
      details: {
        image: "redis:7-alpine",
        status: "running",
        created: "19 days ago (2026-05-24)",
        restarts: "0",
        ports: "6379/tcp",
        network: "backend",
        ip: "172.22.0.10",
        mounts: "redis-cache->/data",
      },
    },
    {
      name: "worker",
      id: "3c4d5e6f7a8b",
      pids: "7",
      net: [60, 40],
      block: [120, 200],
      cpu: (t) => Math.max(6, 50 + 48 * Math.sin((2 * Math.PI * t) / 60 - 1.1)),
      mem: (t) => 14.5 + 1.2 * Math.sin((2 * Math.PI * t) / 30 + 0.7),
      details: {
        image: "ghcr.io/p3rception/job-worker:demo",
        status: "running",
        created: "2 days ago (2026-06-10)",
        restarts: "2",
        ports: "",
        network: "backend",
        ip: "172.22.0.11",
        mounts: "/tmp/jobs->/var/lib/jobs",
      },
    },
  ];

  function createState(overrides) {
    return Object.assign(
      {
        sortKey: "cpu",
        sortAsc: false,
        filterText: "",
        filterTyping: false,
        filterCursor: 0,
        topN: 0,
        highlight: false,
        selectedId: "",
        visibleIds: [],
        detailView: false,
        showId: false,
        showHelp: false,
        barMode: "hint",
        paused: false,
        pausedRows: [],
        pausedTick: TICK_OFFSET,
        lastTick: TICK_OFFSET,
        lastRows: [],
        lastWidth: 140,
        quit: false,
      },
      overrides || {},
    );
  }

  function wrapTick(tick) {
    const value = Math.floor(Number.isFinite(tick) ? tick : 0);
    return ((value % CYCLES) + CYCLES) % CYCLES;
  }

  function clampPercent(value) {
    return Math.max(0, Math.min(999, value));
  }

  function formatBytes(kb) {
    if (kb >= 1000000) {
      return `${(kb / 1000000).toFixed(2)}GB`;
    }
    if (kb >= 1000) {
      return `${(kb / 1000).toFixed(1)}MB`;
    }
    return `${kb.toFixed(0)}kB`;
  }

  function formatUsage(memGib) {
    if (memGib >= 1) {
      return `${memGib.toFixed(3)}GiB`;
    }
    return `${(memGib * 1024).toFixed(1)}MiB`;
  }

  function makeRows(tick) {
    const t = wrapTick(tick);
    return containers.map((container) => {
      const cpuVal = clampPercent(container.cpu(t));
      const memVal = clampPercent(container.mem(t));
      const memGib = (MEM_TOTAL_GIB * memVal) / 100;
      return {
        id: container.id,
        name: container.name,
        cpuRaw: `${cpuVal.toFixed(2)}%`,
        cpuVal,
        memRaw: `${memVal.toFixed(2)}%`,
        memVal,
        memUsage: `${formatUsage(memGib)} / ${MEM_TOTAL_GIB.toFixed(3)}GiB`,
        memUsedGib: memGib,
        memLimitGib: MEM_TOTAL_GIB,
        netIo: `${formatBytes(container.net[0] * (t + 40))} / ${formatBytes(container.net[1] * (t + 40))}`,
        blockIo: `${formatBytes(container.block[0] * (t + 40))} / ${formatBytes(container.block[1] * (t + 40))}`,
        pids: container.pids,
      };
    });
  }

  function parseFilterExpr(filterText) {
    const groups = String(filterText || "")
      .toLowerCase()
      .split("|")
      .map((group) =>
        group
          .split("&&")
          .flatMap((chunk) => chunk.trim().split(/\s+/))
          .filter(Boolean),
      )
      .filter((terms) => terms.length > 0);
    return groups;
  }

  function filterRows(rows, filterText) {
    const groups = parseFilterExpr(filterText);
    if (!groups.length) {
      return rows;
    }
    return rows.filter((row) => {
      const haystack = row.name.toLowerCase();
      return groups.some((terms) => terms.every((term) => haystack.includes(term)));
    });
  }

  function compareValues(a, b, key, asc) {
    let result = 0;
    if (key === "cpu") {
      result = a.cpuVal - b.cpuVal;
    } else if (key === "mem") {
      result = a.memVal - b.memVal;
    } else if (key === "name") {
      result = a.name.localeCompare(b.name);
    }
    return asc ? result : -result;
  }

  function displayRows(state, tick) {
    const rawRows = state.paused ? state.pausedRows.slice() : makeRows(tick);
    let rows = rawRows.slice();
    if (state.sortKey !== "none") {
      rows.sort((a, b) => compareValues(a, b, state.sortKey, state.sortAsc));
    }
    rows = filterRows(rows, state.filterText);
    if (state.topN > 0) {
      rows = rows.slice(0, state.topN);
    }
    return rows;
  }

  function sortLabel(state) {
    if (state.sortKey === "none") {
      return "-";
    }
    const arrow = state.sortAsc ? "↑" : "↓";
    if (state.sortKey === "name") {
      return `name ${arrow}`;
    }
    return `${state.sortKey} % ${arrow}`;
  }

  function columnsFor(width, showId) {
    const termWidth = Number.isFinite(width) ? width : 140;
    const columns = ["name"];
    if (showId) {
      columns.push("id");
    }
    columns.push("cpu", "cpuBar", "mem", "memBar");
    if (termWidth >= 90) {
      columns.push("memUsage");
    }
    if (termWidth >= 108) {
      columns.push("netIo");
    }
    if (termWidth >= 135) {
      columns.push("blockIo");
    }
    return columns;
  }

  function fakeClock(tick) {
    const t = wrapTick(tick);
    return `14:55:${String(t).padStart(2, "0")}`;
  }

  // Mirrors _fmt_bytes() in dsw/render.py: 3 significant digits below 100,
  // integers at or above it.
  function formatTotalBytes(gib) {
    let value = gib;
    let unit = "GiB";
    if (value < 1) {
      value *= 1024;
      unit = "MiB";
    }
    const text = value >= 100 ? value.toFixed(0) : Number(value.toPrecision(3)).toString();
    return `${text}${unit}`;
  }

  function totalsFor(rows) {
    const totalCpu = rows.reduce((sum, row) => sum + row.cpuVal, 0);
    const used = rows.reduce((sum, row) => sum + row.memUsedGib, 0);
    const limit = rows.reduce((sum, row) => sum + row.memLimitGib, 0);
    const memPct = limit > 0 ? (used / limit) * 100 : 0;
    // All demo containers share one limit string, so like the real app the
    // usage column shows the summed usage against that single limit.
    return {
      name: "TOTAL",
      cpuRaw: `${totalCpu.toFixed(2)}%`,
      cpuVal: Math.min(totalCpu, 100),
      memRaw: `${memPct.toFixed(2)}%`,
      memVal: Math.min(memPct, 100),
      memUsage: `${formatTotalBytes(used)} / ${MEM_TOTAL_GIB.toFixed(3)}GiB`,
      netIo: "-",
      blockIo: "-",
    };
  }

  function detailsFor(id) {
    const item = containers.find((container) => container.id === id);
    if (!item) {
      return null;
    }
    return Object.assign({ id: item.id }, item.details);
  }

  function historyFor(id, tick, width) {
    const item = containers.find((container) => container.id === id);
    if (!item) {
      return [];
    }
    const size = width || HISTORY_WIDTH;
    const end = wrapTick(tick);
    const values = [];
    for (let i = size - 1; i >= 0; i -= 1) {
      const t = wrapTick(end - i);
      values.push({
        cpu: clampPercent(item.cpu(t)),
        mem: clampPercent(item.mem(t)),
      });
    }
    return values;
  }

  function getFrame(state, tick, width) {
    const effectiveTick = state.paused ? state.pausedTick : wrapTick((tick || 0) + TICK_OFFSET);
    const termWidth = Number.isFinite(width) ? width : state.lastWidth || 140;
    const rawRows = state.paused ? state.pausedRows.slice() : makeRows(effectiveTick);
    if (!state.paused) {
      state.lastTick = effectiveTick;
      state.lastRows = rawRows.slice();
    }
    state.lastWidth = termWidth;

    const rows = displayRows(state, effectiveTick);
    const visibleIds = rows.map((row) => row.id);
    state.visibleIds = visibleIds;

    if (state.quit) {
      return {
        mode: "quit",
        header: "dsw session ended",
        rows: [],
        visibleIds: [],
      };
    }

    if (state.showHelp) {
      return {
        mode: "help",
        header: "press h to return",
        visibleIds,
      };
    }

    const selectedRow = rows.find((row) => row.id === state.selectedId);
    if (state.detailView && selectedRow) {
      return {
        mode: "detail",
        row: selectedRow,
        details: detailsFor(selectedRow.id),
        history: historyFor(selectedRow.id, effectiveTick),
        visibleIds,
      };
    }

    const count = rows.length;
    const headerSegments = [
      [
        { style: "value", text: String(count) },
        { style: "muted", text: ` container${count === 1 ? "" : "s"}` },
      ],
      [
        { style: "muted", text: "sorted by " },
        { style: "value", text: sortLabel(state) },
      ],
      state.paused
        ? [{ style: "warning", text: " PAUSED " }]
        : [
            { style: "muted", text: "last update " },
            { style: "value", text: fakeClock(effectiveTick) },
          ],
    ];
    if (state.filterText || (state.filterTyping && state.barMode !== "bar")) {
      const filterSegment = [{ style: "muted", text: "filter: " }];
      if (state.filterTyping && state.barMode !== "bar") {
        const cursor = clampCursor(state);
        filterSegment.push(
          { style: "filter", text: `'${state.filterText.slice(0, cursor)}` },
          { style: "cursor", text: "|" },
          { style: "filter", text: `${state.filterText.slice(cursor)}'` },
        );
      } else {
        filterSegment.push({ style: "filter", text: `'${state.filterText}'` });
      }
      headerSegments.push(filterSegment);
    }
    if (state.topN > 0) {
      headerSegments.push([
        { style: "muted", text: "top " },
        { style: "value", text: String(state.topN) },
      ]);
    }

    return {
      mode: "stats",
      header: headerSegments
        .map((segment) => segment.map((part) => part.text).join(""))
        .join(" | "),
      headerSegments,
      rows,
      totals: totalsFor(rows),
      columns: columnsFor(termWidth, state.showId),
      keyPrompt: state.filterTyping
        ? { mode: "filter", text: state.filterText, cursor: clampCursor(state) }
        : { mode: "keys" },
      visibleIds,
    };
  }

  function ensureVisibleIds(state) {
    if (state.visibleIds.length) {
      return state.visibleIds.slice();
    }
    return displayRows(state, state.lastTick || TICK_OFFSET).map((row) => row.id);
  }

  function moveSelection(state, direction) {
    const ids = ensureVisibleIds(state);
    if (!ids.length) {
      state.selectedId = "";
      return;
    }
    if (!state.highlight) {
      state.highlight = true;
      state.selectedId = state.selectedId || ids[0];
      return;
    }
    // Mirrors _apply_escape_sequence() in dsw/loops.py: when the selection is
    // no longer visible, either direction lands on the first row.
    const index = ids.indexOf(state.selectedId);
    let next;
    if (direction < 0) {
      next = index > 0 ? index - 1 : 0;
    } else {
      next = Math.min(ids.length - 1, index >= 0 ? index + 1 : 0);
    }
    state.selectedId = ids[next];
  }

  function isPrintableKey(key) {
    return typeof key === "string" && key.length === 1 && key >= " ";
  }

  const COMMAND_KEY_BY_POSITION = new Map([
    [";", "q"],
    ["ς", "w"],
    ["ε", "e"],
    ["ρ", "r"],
    ["τ", "t"],
    ["υ", "y"],
    ["θ", "u"],
    ["ι", "i"],
    ["ο", "o"],
    ["π", "p"],
    ["α", "a"],
    ["σ", "s"],
    ["δ", "d"],
    ["φ", "f"],
    ["γ", "g"],
    ["η", "h"],
    ["ξ", "j"],
    ["κ", "k"],
    ["λ", "l"],
    ["ζ", "z"],
    ["χ", "x"],
    ["ψ", "c"],
    ["ω", "v"],
    ["β", "b"],
    ["ν", "n"],
    ["μ", "m"],
    ["й", "q"],
    ["ц", "w"],
    ["у", "e"],
    ["к", "r"],
    ["е", "t"],
    ["н", "y"],
    ["г", "u"],
    ["ш", "i"],
    ["щ", "o"],
    ["з", "p"],
    ["х", "["],
    ["ъ", "]"],
    ["ф", "a"],
    ["ы", "s"],
    ["в", "d"],
    ["а", "f"],
    ["п", "g"],
    ["р", "h"],
    ["о", "j"],
    ["л", "k"],
    ["д", "l"],
    ["ж", ";"],
    ["э", "'"],
    ["я", "z"],
    ["ч", "x"],
    ["с", "c"],
    ["м", "v"],
    ["и", "b"],
    ["т", "n"],
    ["ь", "m"],
    ["б", ","],
    ["ю", "."],
    [".", "/"],
  ]);

  function normalizeCommandKey(key) {
    if (typeof key !== "string" || key.length !== 1) {
      return key;
    }
    const lower = key.toLowerCase();
    return COMMAND_KEY_BY_POSITION.get(lower) || lower;
  }

  function clampCursor(state) {
    const cursor = Number.isFinite(state.filterCursor) ? state.filterCursor : state.filterText.length;
    return Math.max(0, Math.min(state.filterText.length, cursor));
  }

  function applyFilterKey(state, key) {
    if (key === "Enter" || key === "\r" || key === "\n") {
      state.filterTyping = false;
    } else if (key === "Escape") {
      state.filterTyping = false;
      state.filterText = "";
      state.filterCursor = 0;
    } else if (key === "ArrowLeft") {
      state.filterCursor = Math.max(0, clampCursor(state) - 1);
    } else if (key === "ArrowRight") {
      state.filterCursor = Math.min(state.filterText.length, clampCursor(state) + 1);
    } else if (key === "Backspace") {
      const cursor = clampCursor(state);
      if (cursor > 0) {
        state.filterText = state.filterText.slice(0, cursor - 1) + state.filterText.slice(cursor);
        state.filterCursor = cursor - 1;
      } else {
        state.filterCursor = 0;
      }
    } else if (key === "Delete") {
      const cursor = clampCursor(state);
      if (cursor < state.filterText.length) {
        state.filterText = state.filterText.slice(0, cursor) + state.filterText.slice(cursor + 1);
      }
      state.filterCursor = cursor;
    } else if (key === "") {
      state.quit = true;
    } else if (isPrintableKey(key)) {
      const cursor = clampCursor(state);
      state.filterText = state.filterText.slice(0, cursor) + key + state.filterText.slice(cursor);
      state.filterCursor = cursor + key.length;
    }
  }

  function applyKey(state, key) {
    if (state.filterTyping) {
      applyFilterKey(state, key);
      return state;
    }

    const commandKey = normalizeCommandKey(key);

    if (commandKey === "q" || commandKey === "") {
      state.quit = true;
    } else if (commandKey === "c") {
      state.sortAsc = state.sortKey === "cpu" ? !state.sortAsc : false;
      state.sortKey = "cpu";
    } else if (commandKey === "m") {
      state.sortAsc = state.sortKey === "mem" ? !state.sortAsc : false;
      state.sortKey = "mem";
    } else if (commandKey === "n") {
      state.sortAsc = state.sortKey === "name" ? !state.sortAsc : false;
      state.sortKey = "name";
    } else if (commandKey === "r") {
      state.sortKey = "none";
      state.sortAsc = false;
      state.filterText = "";
      state.filterTyping = false;
      state.filterCursor = 0;
      state.quit = false;
    } else if (commandKey === "i") {
      state.showId = !state.showId;
    } else if (commandKey === "h") {
      state.showHelp = !state.showHelp;
    } else if (commandKey === "b") {
      // ponytail: hint is one-way; "bar" -> "none" -> "bar", hint never returns
      state.barMode = state.barMode !== "bar" ? "bar" : "none";
    } else if (commandKey === " ") {
      state.paused = !state.paused;
      if (state.paused) {
        state.pausedTick = state.lastTick || TICK_OFFSET;
        state.pausedRows = state.lastRows.length ? state.lastRows.slice() : makeRows(state.pausedTick);
      }
    } else if (commandKey === "/") {
      state.filterTyping = true;
      state.filterCursor = state.filterText.length;
    } else if (commandKey === "l") {
      state.highlight = !state.highlight;
      if (state.highlight && !state.selectedId) {
        // Mirrors keyboard_loop() in dsw/loops.py: auto-select the first row
        // in fetch order, not display order.
        const src = state.paused
          ? state.pausedRows
          : state.lastRows.length
            ? state.lastRows
            : makeRows(state.lastTick);
        state.selectedId = src.length ? src[0].id : "";
      } else if (!state.highlight) {
        state.selectedId = "";
      }
    } else if (commandKey === "ArrowUp") {
      moveSelection(state, -1);
    } else if (commandKey === "ArrowDown") {
      moveSelection(state, 1);
    } else if (commandKey === "Enter" || commandKey === "\r" || commandKey === "\n") {
      const ids = ensureVisibleIds(state);
      if (ids.length) {
        if (!state.selectedId || !ids.includes(state.selectedId)) {
          state.selectedId = ids[0];
        }
        state.detailView = !state.detailView;
      }
    } else if (commandKey === "Escape") {
      if (state.detailView) {
        state.detailView = false;
      } else {
        state.selectedId = "";
      }
    }
    return state;
  }

  function getSelectedRow(state, tick, width) {
    const frame = getFrame(state, tick, width);
    if (frame.mode !== "stats" && frame.mode !== "detail") {
      return null;
    }
    const rows = frame.mode === "detail" ? displayRows(state, state.lastTick) : frame.rows;
    return rows.find((row) => row.id === state.selectedId) || null;
  }

  return {
    createState,
    applyKey,
    normalizeCommandKey,
    getFrame,
    getSelectedRow,
    historyFor,
    parseFilterExpr,
    filterRows,
    formatBytes,
    makeRows,
  };
});
