"""Repo-root entry point so the checkout is directly runnable.

`python3 /path/to/dsw` (or the launcher that `make install` writes) executes
this file with the repo on sys.path, so `dsw` resolves as a package and its
relative imports work - no install step, no PYTHONPATH. `python3 -m dsw` still
works too.
"""

from dsw.__main__ import main

main()
