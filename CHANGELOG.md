# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-09-07

### Added

- Live, colorized terminal dashboard for `docker stats` that repaints in place instead of taking over the screen, so the output stays in your scrollback when you quit.
- Sort by CPU, memory, or container name, from `--sort` at launch or the `c` / `m` / `n` keys while running; `r` resets sort and filter.
- Container name filtering: `--filter`, the positional shortcut (`dsw postgres`), or `/` to type interactively. Terms combine with space or `&&` for AND and `|` for OR, matched case-insensitively as substrings.
- `--top N` to show only the busiest N containers.
- Per-container detail view: press Enter on a row for image, status, created time, restart count, port mappings, mounts, network, and IP address, with CPU and memory sparklines. `docker inspect` is called only while the view is open, and the result is cached.
- Row highlight and arrow-key selection (`--highlight`, `l`, Up/Down), pause/resume with Space, and a help overlay on `h`.
- Bottom key bar that starts as a compact `b for shortcuts` hint and cycles hint -> full -> off with `b`; `--hide-bar` starts with it off.
- Totals row aggregating CPU and memory across every container, and heat-map colored usage bars.
- Responsive column layout: wider terminals add MEM USAGE, then NET I/O, then BLOCK I/O.
- Short container ID column via `--show-id` or the `i` key, handy for `docker exec` and `docker logs`.
- Podman support via `--runtime podman`.
- Config file at `$XDG_CONFIG_HOME/dsw/config.toml` (default `~/.config/dsw/config.toml`) for persisting any of `sort`, `filter`, `runtime`, `top`, `highlight`, `show_id`, `no_color`, and `hide_bar`. CLI flags override it; unknown keys and malformed files warn on stderr rather than failing startup. `--init` writes a commented template.
- 24-bit color when the terminal advertises truecolor via `COLORTERM`, with a 256-color fallback otherwise. Disabled by `--no-color`, `NO_COLOR`, `CLICOLOR=0`, or `TERM=dumb`.
- Install without pip: `make install` drops a launcher on `PATH` that runs the checkout in place, so updating is `git pull`. The repo root is also directly runnable with `python3 /path/to/dsw`.
- Zero runtime dependencies - standard library only, Python 3.11+, Linux and macOS.

### Security

- Everything the container runtime reports is treated as untrusted: container names, stats fields, and the detail view's mount paths, port bindings, network names, and IP addresses have ANSI escape sequences and control characters stripped before rendering, including whole OSC/DCS/SOS/PM/APC payloads. `docker inspect` is invoked with a `--` argument terminator so an ID beginning with `-` cannot be read as a flag.
