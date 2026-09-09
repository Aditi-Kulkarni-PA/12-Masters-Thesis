"""Command-line entry points for the topology harness.

Every module here is a script with its own `__main__` block, invoked through the shell
scripts in `scripts/`. The importable modules they draw on live one level up, in
`measurement/`, `analysis/`, `topologies/`, `core/`, `tools/`, `helpers/` and `config/`.

Each script re-roots `sys.path` onto the app package (`parent.parent` from here), so it
can be run directly as `python cli/<name>.py` from the repository root. Sibling imports
between these scripts work because Python places the script's own directory first on
`sys.path`.
"""
