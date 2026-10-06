"""Read-only views of the data root for the control room, one module per area.

Rules (documentation/control-room/README.md §4–§5): never write under the data root, never
read an env or credential file, scrub every free text before it leaves, and cope with weeks
that ran before the run records existed (2026 week 4 has no `run_summary.json`).
"""
