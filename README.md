# dsw

`dsw` (docker-stats-wrapper) is a nicer `docker stats`: inline, colored, sortable,
filterable - without a full-screen takeover.

**[Try the interactive demo](https://dimitrisper.xyz/docker-stats-wrapper/)** and explore
dsw in your browser, keyboard shortcuts included, no install needed.

Inspired by [ctop](https://github.com/bcicen/ctop), but built to stay a drop-in
upgrade for `docker stats` rather than a full-screen container manager.

## Installation

No pip, no build step:

```bash
git clone https://github.com/p3rception/docker-stats-wrapper.git
cd docker-stats-wrapper
make install
```

The launcher lands in `~/.local/bin/dsw` (override with `make install
PREFIX=/usr/local`) and runs the checkout in place, so **updating is `git pull`** and
removal is `make uninstall`. If `~/.local/bin` is not on your `PATH`, `make install`
tells you.

Or skip the launcher and run the folder directly:

```bash
python3 /path/to/dsw
```

## Usage

```bash
# Well, you know
dsw

# Spot the CPU hog: the 10 busiest containers, sorted live
dsw --sort cpu --top 10

# Hunt a memory leak
dsw --sort mem

# Watch your whole *arr stack at once (substring match: sonarr, radarr, prowlarr, ...)
dsw arr

# Tail a media + download stack ( | matches any term)
dsw "jellyfin|qbittorrent|sabnzbd"

# Just the datastores, with short IDs handy for docker exec / logs
dsw --show-id "postgres|mariadb|redis"

# Homelab running Podman instead of Docker
dsw --runtime podman

# Save these preferences so a bare `dsw` always starts this way
dsw --init && $EDITOR ~/.config/dsw/config.toml
```

### CLI flags

| Flag                | Description                                                          |
| ------------------- | -------------------------------------------------------------------- |
| `--sort KEY`        | Initial sort: `cpu`, `mem`, `name`, `none` (default: `cpu`)          |
| `--filter STR`      | Pre-set a container name filter on startup                           |
| `NAME` (positional) | Shortcut for `--filter NAME`, e.g. `dsw postgres`                    |
| `--runtime KEY`     | Container runtime to query: `docker` or `podman` (default: `docker`) |
| `--top N`           | Show only the top N containers (default: 0 = all)                    |
| `--highlight`       | Start with row highlight and arrow-key selection                     |
| `--show-id`         | Show the short container ID column                                   |
| `--no-color`        | Disable ANSI color output                                            |
| `--hide-bar`        | Start with the bottom key bar hidden (toggle back with `b`)          |
| `--init`            | Write a commented config template to the config path and exit        |
| `-V`, `--version`   | Show version and exit                                                |
| `-h`, `--help`      | Show help and exit                                                   |

### Keyboard shortcuts

| Key            | Action                                                                               |
| -------------- | ------------------------------------------------------------------------------------ |
| `c`            | Sort by CPU %                                                                        |
| `m`            | Sort by memory %                                                                     |
| `n`            | Sort by container name                                                               |
| `r`            | Reset sort order and clear filter                                                    |
| `i`            | Toggle container ID column                                                           |
| `Space`        | Pause / resume display                                                               |
| `/`            | Enter filter mode (type to filter)                                                   |
| `Enter`        | Open / close the detail view for the selected container; confirm filter while typing |
| `Esc`          | Close detail view / cancel filter / clear selection                                  |
| `Up` / `Down`  | Enable highlight mode / move row selection                                           |
| `l`            | Toggle row highlight                                                                 |
| `b`            | Cycle key bar: hint -> full -> off                                                   |
| `h`            | Show / hide help overlay                                                             |
| `q` / `Ctrl-C` | Quit                                                                                 |

### Configuration file

Persist defaults in `$XDG_CONFIG_HOME/dsw/config.toml` (default
`~/.config/dsw/config.toml`). Run `dsw --init` to write a commented template with
every option (it won't overwrite an existing one), then uncomment what you want.
Keys are flag names with dashes replaced by underscores:

```toml
sort = "mem"      # cpu | mem | name | none
runtime = "podman"
top = 10
highlight = true
show_id = true
no_color = false
hide_bar = true
filter = "web"
```

CLI flags override the config file. Unknown keys and malformed files are reported
on stderr and ignored rather than failing startup.

### Filter syntax

Filters match container names as case-insensitive substrings and support multiple terms:

- Terms separated by a space or `&&` must **all** match (AND): `web && prod`
- Groups separated by `|` match **any** group (OR): `nginx|postgres`

The same syntax works for the `--filter` flag, the positional shortcut, and the interactive `/` filter.

## Support matrix

|            | Supported                                                             |
| ---------- | --------------------------------------------------------------------- |
| **Python** | 3.11, 3.12, 3.13                                                      |
| **OS**     | Linux, macOS                                                          |
| **Docker** | Any version with `docker stats --format` support (CLI on `PATH`)      |
| **Podman** | Any version with `podman stats --format` support (`--runtime podman`) |

> **Windows is not supported.** dsw uses Unix-specific terminal APIs (`termios`, `tty`).

Notes:

- Wider terminals show more columns: MEM USAGE, then NET I/O, then BLOCK I/O.
- Colors are 24-bit when the terminal advertises truecolor via `COLORTERM`, with a
  256-color fallback otherwise. Disabled by `--no-color`, `NO_COLOR`
  ([no-color.org](https://no-color.org)), `CLICOLOR=0`, or `TERM=dumb`.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and guidelines.

## License

[MIT](LICENSE)
