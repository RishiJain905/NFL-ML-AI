"""Weekly operations (P07): the calendar, the run lock, service start-up, the run summary and
pipeline W&B run, drift checks, the season dashboard and the Saturday injury update.

Runs stay manual (D71): nothing here schedules anything. The pieces make a hands-on
`nfl weekly run --auto` safe (lock, calendar, fail-soft services), observable (run summary,
pipeline history, `weekly-pipeline` / `pipeline` W&B run, season dashboard) and
self-checking (drift alerts), so it can be scheduled later without changes.
"""
