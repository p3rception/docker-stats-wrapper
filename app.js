(function runDswDemo() {
  "use strict";

  const core = window.DswDemoCore;
  const screen = document.getElementById("terminalScreen");
  const dswBtn = document.getElementById("dswBtn");
  const rawBtn = document.getElementById("rawBtn");

  let state = core.createState();
  let tick = 0;
  let lastFrameMode = "stats";
  let rawMode = false;

  const columnMeta = {
    name: { label: "CONTAINER", align: "left", sortKey: "name" },
    id: { label: "ID", align: "left" },
    cpu: { label: "CPU %", align: "right", sortKey: "cpu" },
    cpuBar: { label: "", align: "left" },
    mem: { label: "MEM %", align: "right", sortKey: "mem" },
    memBar: { label: "", align: "left" },
    memUsage: { label: "MEM USAGE", align: "right" },
    netIo: { label: "NET I/O", align: "right" },
    blockIo: { label: "BLOCK I/O", align: "right" },
  };

  const keySections = [
    {
      label: "Sort:",
      keys: [
        ["n", "name"],
        ["c", "cpu"],
        ["m", "mem"],
        ["r", "reset"],
      ],
    },
    {
      label: "View:",
      keys: [
        ["Enter", "details"],
        ["/", "filter"],
        ["l", "highlight"],
        ["i", "id"],
        [" ", "pause"],
        ["b", "bar"],
      ],
    },
    {
      label: "",
      keys: [
        ["h", "help"],
        ["q", "quit"],
      ],
    },
  ];

  const helpSections = [
    {
      title: "Sort",
      rows: [
        ["c", "by CPU"],
        ["m", "by memory"],
        ["n", "by name"],
        ["r", "reset + clear filter"],
      ],
    },
    {
      title: "Select & filter",
      rows: [
        ["↑↓", "move selection"],
        ["l", "toggle highlight"],
        ["enter", "open / close detail"],
        ["/", "start filter"],
        ["esc", "back / clear / deselect"],
      ],
    },
    {
      title: "Display",
      rows: [
        ["space", "pause / resume"],
        ["i", "toggle ID column"],
        ["b", "toggle key bar"],
        ["h", "close this help"],
        ["q", "quit"],
      ],
    },
  ];

  function clearScreen() {
    screen.textContent = "";
  }

  function append(parent, tagName, className, text) {
    const node = document.createElement(tagName);
    if (className) {
      node.className = className;
    }
    if (text !== undefined) {
      node.textContent = text;
    }
    parent.appendChild(node);
    return node;
  }

  function blankLine(parent) {
    append(parent, "div", "screen-line", "");
  }

  function estimateTermWidth() {
    const probe = append(screen, "span", "measure-probe", "0".repeat(40));
    const charWidth = probe.getBoundingClientRect().width / 40 || 8;
    probe.remove();
    const styles = getComputedStyle(screen);
    const usable =
      screen.clientWidth -
      Number.parseFloat(styles.paddingLeft) -
      Number.parseFloat(styles.paddingRight);
    return Math.max(70, Math.floor(usable / charWidth));
  }

  function heatClass(value) {
    if (value >= 80) {
      return "heat-red";
    }
    if (value >= 60) {
      return "heat-orange";
    }
    if (value >= 40) {
      return "heat-yellow";
    }
    return "heat-green";
  }

  const headerStyles = {
    value: "header-value",
    muted: "header-muted",
    warning: "header-warning",
    filter: "header-filter",
    cursor: "header-cursor",
  };

  function renderHeader(parent, frame) {
    const line = append(parent, "div", "screen-line header-line");
    append(line, "span", "header-leader", "▶");
    frame.headerSegments.forEach((segment, index) => {
      if (index > 0) {
        append(line, "span", "header-separator", "|");
      }
      const holder = append(line, "span", "header-segment");
      segment.forEach((part) => {
        append(holder, "span", headerStyles[part.style] || "", part.text);
      });
    });
  }

  function columnWidths(frame) {
    const barWidth = frame.columns.includes("netIo") ? 12 : 6;
    const nameWidth = Math.max(9, ...frame.rows.map((row) => row.name.length));
    return {
      name: nameWidth,
      id: 12,
      cpu: 8,
      cpuBar: barWidth,
      mem: 8,
      memBar: barWidth,
      memUsage: 22,
      netIo: 18,
      blockIo: 18,
    };
  }

  function gridTemplate(columns, widths) {
    return columns.map((column) => `${widths[column]}ch`).join(" ") + " minmax(0, 1fr)";
  }

  function cell(parent, column, text, extraClass) {
    const meta = columnMeta[column];
    const classNames = ["cell"];
    if (meta.align === "right") {
      classNames.push("right");
    }
    if (extraClass) {
      classNames.push(extraClass);
    }
    return append(parent, "div", classNames.join(" "), text);
  }

  function renderSortLabel(parent, column) {
    const meta = columnMeta[column];
    const isActive = meta.sortKey && state.sortKey === meta.sortKey;
    const node = cell(parent, column, meta.label, isActive ? "active-sort" : "");
    if (isActive) {
      append(node, "span", "sort-arrow", state.sortAsc ? "↑" : "↓");
    }
    return node;
  }

  function renderSeparator(parent, columns, widths) {
    const row = append(parent, "div", "demo-row separator");
    row.style.gridTemplateColumns = gridTemplate(columns, widths);
    columns.forEach((column) => {
      cell(row, column, "─".repeat(widths[column]));
    });
  }

  function renderTotalsSeparator(parent, columns, widths) {
    const row = append(parent, "div", "demo-row separator");
    row.style.gridTemplateColumns = gridTemplate(columns, widths);
    const total =
      columns.reduce((sum, column) => sum + widths[column], 0) +
      2 * (columns.length - 1);
    const node = cell(row, columns[0], "─".repeat(total));
    node.style.gridColumn = `1 / span ${columns.length}`;
  }

  // Mirrors _bar() in dsw/render.py: full blocks, a fractional eighths cell,
  // then the empty track drawn with ▏ characters.
  const EIGHTHS = " ▏▎▍▌▋▊▉█";

  function renderBar(parent, column, value, width) {
    const holder = cell(parent, column, "", "bar");
    holder.setAttribute("aria-label", `${value.toFixed(1)} percent`);
    const filled = Math.max(0, Math.min(width, (value / 100) * width));
    const nFull = Math.floor(filled);
    const frac = EIGHTHS[Math.min(8, Math.round((filled - nFull) * 8))];
    const hasFrac = frac !== " ";
    const nEmpty = Math.max(0, width - nFull - (hasFrac ? 1 : 0));
    const fill = "█".repeat(nFull) + (hasFrac ? frac : "");
    if (fill) {
      append(holder, "span", heatClass(value), fill);
    }
    if (nEmpty) {
      append(holder, "span", "bar-track", "▏".repeat(nEmpty));
    }
  }

  function renderRow(parent, frame, widths, rowData, type) {
    const row = append(parent, "div", `demo-row ${type}`);
    row.style.gridTemplateColumns = gridTemplate(frame.columns, widths);

    if (type === "data" && state.highlight && state.selectedId === rowData.id) {
      row.classList.add("selected");
    }

    frame.columns.forEach((column) => {
      if (column === "name") {
        cell(row, column, rowData.name, type === "total" ? "dim" : "");
      } else if (column === "id") {
        cell(row, column, rowData.id || "", "dim");
      } else if (column === "cpu") {
        cell(row, column, rowData.cpuRaw, `pct ${heatClass(rowData.cpuVal)}`);
      } else if (column === "cpuBar") {
        renderBar(row, column, rowData.cpuVal, widths.cpuBar);
      } else if (column === "mem") {
        cell(row, column, rowData.memRaw, `pct ${heatClass(rowData.memVal)}`);
      } else if (column === "memBar") {
        renderBar(row, column, rowData.memVal, widths.memBar);
      } else if (column === "memUsage") {
        cell(row, column, rowData.memUsage, "dim");
      } else if (column === "netIo") {
        cell(row, column, rowData.netIo, "dim");
      } else if (column === "blockIo") {
        cell(row, column, rowData.blockIo, "dim");
      }
    });
  }

  function renderTable(parent, frame) {
    const widths = columnWidths(frame);
    const table = append(parent, "div", "table-wrap");
    const header = append(table, "div", "demo-row header");
    header.style.gridTemplateColumns = gridTemplate(frame.columns, widths);
    frame.columns.forEach((column) => renderSortLabel(header, column));
    renderSeparator(table, frame.columns, widths);

    if (!frame.rows.length) {
      append(table, "div", "screen-line dim", "  no containers running");
    } else {
      frame.rows.forEach((row) => renderRow(table, frame, widths, row, "data"));
      renderTotalsSeparator(table, frame.columns, widths);
      renderRow(table, frame, widths, frame.totals, "total");
    }
  }

  function keyLabel(key) {
    if (key === " ") {
      return "space";
    }
    if (key === "Enter") {
      return "enter";
    }
    if (key === "Escape") {
      return "esc";
    }
    return key;
  }

  function renderKeyButton(parent, key) {
    const button = append(parent, "button", "key-button", keyLabel(key));
    button.type = "button";
    button.dataset.key = key;
    return button;
  }

  function renderKeyBar(parent, frame) {
    if (state.barMode === "none") {
      return;
    }
    if (state.barMode === "hint") {
      if (!state.filterTyping) {
        blankLine(parent);
        const hint = append(parent, "div", "screen-line key-line");
        renderKeyButton(hint, "b");
        append(hint, "span", "key-desc", "for shortcuts");
      }
      return;
    }
    blankLine(parent);

    if (frame.keyPrompt.mode === "filter") {
      const line = append(parent, "div", "screen-line filter-line");
      append(line, "span", "filter-label", "filter> ");
      const input = append(line, "input", "filter-input");
      input.value = state.filterText;
      input.style.width = `${Math.max(1, state.filterText.length)}ch`;
      input.setAttribute("aria-label", "Filter containers");
      input.autocomplete = "off";
      input.spellcheck = false;
      append(line, "span", "filter-hint", "   Enter confirm   Esc cancel");
      const syncCursor = () => {
        state.filterCursor = input.selectionStart ?? input.value.length;
      };
      input.addEventListener("input", () => {
        state.filterText = input.value;
        syncCursor();
        render();
      });
      input.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === "Escape") {
          event.preventDefault();
          handleKey(event.key);
        }
      });
      input.addEventListener("keyup", syncCursor);
      input.addEventListener("click", syncCursor);
      requestAnimationFrame(() => {
        const cursor = Math.max(0, Math.min(input.value.length, frame.keyPrompt.cursor ?? input.value.length));
        input.focus();
        input.setSelectionRange(cursor, cursor);
      });
      return;
    }

    const line = append(parent, "div", "screen-line key-line");
    keySections.forEach((section, index) => {
      if (section.label) {
        append(line, "span", "key-label", section.label);
      }
      section.keys.forEach(([key, desc]) => {
        renderKeyButton(line, key);
        append(line, "span", "key-desc", desc);
      });
      if (index < keySections.length - 1) {
        append(line, "span", "key-separator", "|");
      }
    });
  }

  function sparkChar(value) {
    const blocks = "▁▂▃▄▅▆▇█";
    const index = Math.min(blocks.length - 1, Math.floor((Math.max(0, Math.min(100, value)) / 100) * blocks.length));
    return blocks[index];
  }

  function renderSpark(parent, history, key) {
    const spark = append(parent, "span", "sparkline");
    history.forEach((point) => {
      append(spark, "span", heatClass(point[key]), sparkChar(point[key]));
    });
  }

  function detailRow(parent, label, value) {
    const row = append(parent, "div", "detail-row");
    append(row, "div", "detail-label", label);
    append(row, "div", "detail-value", value || "-");
  }

  function metricRow(parent, label, value, history, key) {
    const row = append(parent, "div", "metric-row");
    append(row, "div", "detail-label", label);
    const valueNode = append(row, "div", "detail-value");
    append(valueNode, "span", "metric-value", value || "-");
    renderSpark(valueNode, history, key);
  }

  function renderDetail(parent, frame) {
    blankLine(parent);
    const title = append(parent, "div", "screen-line detail-title");
    append(title, "span", "detail-title-leader", "▶  ");
    append(title, "span", "detail-title-text", `${frame.row.name} details`);
    blankLine(parent);

    const section = append(parent, "div", "detail-section");
    detailRow(section, "ID", frame.row.id);
    detailRow(section, "Image", frame.details.image);
    detailRow(section, "Status", frame.details.status);
    detailRow(section, "Created", frame.details.created);
    detailRow(section, "Restarts", frame.details.restarts);
    detailRow(section, "PIDs", frame.row.pids);

    const metrics = append(parent, "div", "detail-section");
    metricRow(metrics, "CPU", frame.row.cpuRaw, frame.history, "cpu");
    metricRow(metrics, "Memory", frame.row.memRaw, frame.history, "mem");
    detailRow(metrics, "Network I/O", frame.row.netIo);
    detailRow(metrics, "Block I/O", frame.row.blockIo);

    const network = append(parent, "div", "detail-section");
    detailRow(network, "Ports", frame.details.ports);
    detailRow(network, "Network", frame.details.network);
    detailRow(network, "IP", frame.details.ip);
    detailRow(network, "Mounts", frame.details.mounts);

    blankLine(parent);
    const line = append(parent, "div", "screen-line key-line");
    renderKeyButton(line, "Escape");
    append(line, "span", "key-desc", "back");
    renderKeyButton(line, "q");
    append(line, "span", "key-desc", "quit");
  }

  function renderHelp(parent) {
    blankLine(parent);
    const head = append(parent, "div", "screen-line key-line");
    append(head, "span", "header-leader", "▶");
    append(head, "span", "dim", "press");
    renderKeyButton(head, "h");
    append(head, "span", "dim", "to return");
    blankLine(parent);

    const grid = append(parent, "div", "help-grid");
    helpSections.forEach((section) => {
      const panel = append(grid, "section", "help-panel");
      const title = append(panel, "div", "help-panel-title");
      append(title, "span", "help-panel-marker", "▌ ");
      append(title, "span", "help-panel-text", section.title);
      section.rows.forEach(([key, desc]) => {
        const row = append(panel, "div", "help-row");
        append(row, "span", "help-badge", key);
        append(row, "span", "dim", desc);
      });
    });

    blankLine(parent);
    append(
      parent,
      "div",
      "screen-line dim",
      "Startup options: run dsw --help for --sort, --filter, --runtime, --top, --highlight, --show-id, --no-color.",
    );
  }

  function renderQuit(parent) {
    blankLine(parent);
    const panel = append(parent, "div", "quit-panel");
    append(panel, "div", "quit-title", "dsw session ended");
    append(panel, "div", "dim", "The static demo has no shell to return to.");
    const button = append(panel, "button", "restart-button", "restart demo");
    button.type = "button";
    button.addEventListener("click", restart);
  }

  // Raw docker stats column widths (characters).
  const RAW_COLS = { id: 12, name: 14, cpu: 9, usage: 22, mem: 8, net: 22, block: 18, pids: 4 };
  const RAW_GAP = "   ";

  function padR(s, w) {
    return String(s).slice(0, w).padEnd(w);
  }

  function padL(s, w) {
    return String(s).slice(0, w).padStart(w);
  }

  function rawLine(row, isHeader) {
    return [
      padR(isHeader ? "CONTAINER ID" : row.id,       RAW_COLS.id),
      padR(isHeader ? "NAME"         : row.name,      RAW_COLS.name),
      padL(isHeader ? "CPU %"        : row.cpuRaw,    RAW_COLS.cpu),
      padL(isHeader ? "MEM USAGE / LIMIT" : row.memUsage, RAW_COLS.usage),
      padL(isHeader ? "MEM %"        : row.memRaw,    RAW_COLS.mem),
      padL(isHeader ? "NET I/O"      : row.netIo,     RAW_COLS.net),
      padL(isHeader ? "BLOCK I/O"    : row.blockIo,   RAW_COLS.block),
      padL(isHeader ? "PIDS"         : row.pids,      RAW_COLS.pids),
    ].join(RAW_GAP);
  }

  function renderRaw() {
    // Use the same effective tick as the running dsw view so the values match.
    const rows = core.makeRows(state.lastTick);
    const lines = [
      rawLine(null, true),
      ...rows.map((row) => rawLine(row, false)),
    ];
    const pre = append(screen, "pre", "raw-output");
    pre.textContent = lines.join("\n");
  }

  function render() {
    // Always call getFrame so state.lastTick stays current even in raw mode.
    const frame = core.getFrame(state, tick, estimateTermWidth());
    lastFrameMode = frame.mode;
    clearScreen();

    if (rawMode) {
      renderRaw();
      return;
    }

    const block = append(screen, "div", "screen-block");

    if (frame.mode === "quit") {
      renderQuit(block);
    } else if (frame.mode === "help") {
      renderHelp(block);
    } else if (frame.mode === "detail") {
      renderDetail(block, frame);
    } else {
      renderHeader(block, frame);
      blankLine(block);
      renderTable(block, frame);
      renderKeyBar(block, frame);
      blankLine(block);
    }
  }

  function restart() {
    state = core.createState();
    tick = 0;
    render();
    screen.focus();
  }

  function handleKey(key) {
    if (state.quit && (key === "r" || key === "Enter")) {
      restart();
      return;
    }
    core.applyKey(state, key);
    render();
  }

  function setRawMode(next) {
    rawMode = next;
    dswBtn.classList.toggle("active", !rawMode);
    rawBtn.classList.toggle("active", rawMode);
    render();
    if (!rawMode) {
      screen.focus();
    }
  }

  dswBtn.addEventListener("click", () => setRawMode(false));
  rawBtn.addEventListener("click", () => setRawMode(true));

  function handleDocumentKeydown(event) {
    if (rawMode) {
      return;
    }
    if (event.metaKey || event.altKey) {
      return;
    }
    if (event.target && event.target.classList && event.target.classList.contains("filter-input")) {
      return;
    }
    const commandKey = core.normalizeCommandKey(event.key);
    const handled = [
      "ArrowUp",
      "ArrowDown",
      "Enter",
      "Escape",
      " ",
      "/",
      "b",
      "c",
      "h",
      "i",
      "l",
      "m",
      "n",
      "q",
      "r",
    ].includes(commandKey);
    const filterEditKey = state.filterTyping && [
      "ArrowLeft",
      "ArrowRight",
      "Backspace",
      "Delete",
    ].includes(event.key);
    if (!handled && !filterEditKey && !(state.filterTyping && event.key.length === 1)) {
      return;
    }
    event.preventDefault();
    handleKey(event.key);
  }

  screen.addEventListener("click", (event) => {
    const button = event.target.closest("[data-key]");
    if (!button) {
      screen.focus();
      return;
    }
    handleKey(button.dataset.key);
  });

  document.addEventListener("keydown", handleDocumentKeydown);
  window.addEventListener("resize", render);

  setInterval(() => {
    if (!state.paused && !state.quit && !state.filterTyping) {
      tick = (tick + 1) % 60;
      render();
    }
  }, 1000);

  render();
  requestAnimationFrame(() => {
    if (lastFrameMode === "stats") {
      screen.focus();
    }
  });
})();
