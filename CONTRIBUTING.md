# Contributing to dsw

Thank you for your interest in contributing to dsw!

## Development setup

```bash
# Clone the repository
git clone https://github.com/p3rception/docker-stats-wrapper.git
cd docker-stats-wrapper

# Create a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# dsw itself needs no install (zero dependencies); only the dev tools do
pip install ruff mypy pytest

# Run the tool straight from the checkout
python3 -m dsw
# or
python3 .
```

## Code standards

- **Python 3.11+** - we use union type syntax (`X | Y`), `tomllib`, and other modern features.
- **Zero dependencies** - the `dsw/` package uses only the Python standard library. Do not add external dependencies without discussion.
- **Linting** - all code must pass `ruff check .` with zero findings.
- **Type checking** - all code must pass `mypy dsw/` with zero errors.
- **Tests** - new features and bug fixes should include tests. Run with `pytest`.

## Making changes

1. Fork the repository and create a feature branch from `main`.
2. Make your changes with clear, descriptive commit messages.
3. Add a bullet to the `## [Unreleased]` section of [CHANGELOG.md](CHANGELOG.md)
   under `Added`, `Changed`, `Fixed`, or `Security`. Describe the user-visible
   effect, not the diff. Code and changelog go in the same commit; the version is
   bumped only at release, never per commit.
4. Ensure all checks pass:
   ```bash
   ruff check .
   mypy dsw/
   pytest
   ```
5. Open a pull request against `main`.

## Commit messages

Use the conventional commit format:

```
<type>(optional-scope): <summary>

[optional body]
```

Types: `feat`, `fix`, `refactor`, `docs`, `test`, `build`, `chore`, `ci`.

## Pull request checklist

- [ ] Code passes `ruff check .` and `mypy dsw/`
- [ ] Tests added or updated for the change
- [ ] CHANGELOG.md updated under `## [Unreleased]`
- [ ] Documentation updated if user-facing behavior changed
- [ ] No new external dependencies introduced

## Reporting bugs

Open a [GitHub issue](https://github.com/p3rception/docker-stats-wrapper/issues/new?template=bug_report.md) with:

- Your OS and terminal emulator
- Python version (`python3 --version`)
- Docker version (`docker --version`)
- Steps to reproduce
- Expected vs. actual behavior

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
