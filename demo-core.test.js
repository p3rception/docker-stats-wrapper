const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const core = require("./demo-core");
const demoDir = __dirname;

test("creates a default dsw-like state sorted by CPU", () => {
  const state = core.createState();
  const frame = core.getFrame(state, 0, 140);

  assert.equal(state.sortKey, "cpu");
  assert.equal(state.sortAsc, false);
  assert.equal(frame.rows[0].name, "worker");
  assert.match(frame.header, /6 containers/);
  assert.match(frame.header, /sorted by cpu % ↓/);
  assert.doesNotMatch(frame.header, /sorted by cpu % down/);
});

test("uses arrow glyphs for each sort direction in the header", () => {
  const state = core.createState();

  assert.match(core.getFrame(state, 0, 140).header, /sorted by cpu % ↓/);

  core.applyKey(state, "c");
  assert.match(core.getFrame(state, 0, 140).header, /sorted by cpu % ↑/);

  core.applyKey(state, "n");
  assert.match(core.getFrame(state, 0, 140).header, /sorted by name ↓/);
});

test("filters rows with OR and AND terms", () => {
  const state = core.createState();
  core.applyKey(state, "/");
  for (const ch of "web|post") {
    core.applyKey(state, ch);
  }
  core.applyKey(state, "Enter");

  const names = core.getFrame(state, 5, 140).rows.map((row) => row.name);

  assert.deepEqual(names.sort(), ["postgres", "web-1", "web-2"]);
  assert.equal(state.filterTyping, false);
  assert.equal(state.filterText, "web|post");
});

test("edits filter text with a cursor", () => {
  const state = core.createState();
  core.applyKey(state, "/");
  for (const ch of "rabit") {
    core.applyKey(state, ch);
  }

  core.applyKey(state, "ArrowLeft");
  core.applyKey(state, "ArrowLeft");
  core.applyKey(state, "b");

  assert.equal(state.filterText, "rabbit");
  assert.equal(state.filterCursor, 4);

  core.applyKey(state, "ArrowRight");
  core.applyKey(state, "Backspace");

  assert.equal(state.filterText, "rabbt");
  assert.equal(state.filterCursor, 4);
});

test("shows filter cursor in header when key bar is hidden", () => {
  const state = core.createState({
    showBar: false,
    filterTyping: true,
    filterText: "abc",
    filterCursor: 2,
  });

  const frame = core.getFrame(state, 0, 140);
  const filterSegment = frame.headerSegments.find((segment) =>
    segment.some((part) => part.text === "filter: "),
  );

  assert.match(frame.header, /filter: 'ab\|c'/);
  assert.deepEqual(filterSegment.slice(1), [
    { style: "filter", text: "'ab" },
    { style: "cursor", text: "|" },
    { style: "filter", text: "c'" },
  ]);
  assert.deepEqual(frame.keyPrompt, { mode: "filter", text: "abc", cursor: 2 });
});

test("uses keyboard position hotkeys outside filter mode", () => {
  const state = core.createState();

  core.applyKey(state, "ψ");
  assert.equal(state.sortKey, "cpu");
  assert.equal(state.sortAsc, true);

  core.applyKey(state, "μ");
  assert.equal(state.sortKey, "mem");

  core.applyKey(state, "ν");
  assert.equal(state.sortKey, "name");

  core.applyKey(state, ";");
  assert.equal(state.quit, true);
});

test("keeps language characters literal while typing a filter", () => {
  const state = core.createState();
  core.applyKey(state, "/");
  core.applyKey(state, "ψ");

  assert.equal(state.filterText, "ψ");
  assert.equal(state.filterCursor, 1);
});

test("selection, detail view, and escape mirror terminal behavior", () => {
  const state = core.createState();

  core.applyKey(state, "ArrowDown");
  assert.equal(state.highlight, true);
  assert.equal(core.getSelectedRow(state, 0, 140).name, "worker");

  core.applyKey(state, "ArrowDown");
  assert.equal(core.getSelectedRow(state, 0, 140).name, "web-1");

  core.applyKey(state, "Enter");
  assert.equal(state.detailView, true);
  assert.equal(core.getFrame(state, 0, 140).mode, "detail");

  core.applyKey(state, "Escape");
  assert.equal(state.detailView, false);
  assert.equal(state.selectedId, "e735e7f0adc5");

  core.applyKey(state, "Escape");
  assert.equal(state.selectedId, "");
});

test("toggles display options and pause snapshot", () => {
  const state = core.createState();

  assert.equal(state.showBar, false);
  core.applyKey(state, "i");
  core.applyKey(state, "b");
  assert.equal(state.showId, true);
  assert.equal(state.showBar, true);

  core.applyKey(state, " ");
  assert.equal(state.paused, true);
  assert.deepEqual(core.getFrame(state, 1, 140).rows, core.getFrame(state, 12, 140).rows);

  core.applyKey(state, " ");
  assert.equal(state.paused, false);
});

test("marks the active sort header cell and keeps the terminal wide", () => {
  const appSource = fs.readFileSync(path.join(demoDir, "app.js"), "utf8");
  const cssSource = fs.readFileSync(path.join(demoDir, "styles.css"), "utf8");

  assert.match(appSource, /active-sort/);
  assert.doesNotMatch(appSource, /else\s*\{\s*blankLine\(block\);\s*renderHeader\(block, frame\);/);
  assert.match(cssSource, /\.active-sort\s*\{/);
  assert.match(cssSource, /\.active-sort[\s\S]*color:\s*var\(--text\)/);
  assert.match(cssSource, /width:\s*min\(100%,\s*1550px\)/);
});

test("browser demo renders narrow gray filter cursors", () => {
  const appSource = fs.readFileSync(path.join(demoDir, "app.js"), "utf8");
  const cssSource = fs.readFileSync(path.join(demoDir, "styles.css"), "utf8");

  assert.match(appSource, /filterCursor/);
  assert.match(appSource, /setSelectionRange/);
  assert.doesNotMatch(appSource, /filter-caret", "█"/);
  assert.match(cssSource, /\.header-cursor\s*\{[\s\S]*color:\s*var\(--muted\)/);
  assert.match(cssSource, /\.filter-input\s*\{[\s\S]*caret-color:\s*var\(--muted\)/);
});
