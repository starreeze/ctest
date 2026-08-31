# Run history

Pipeline invocations append one JSON object per line to `run_history.jsonl`. By default it is stored beside `proxy_failures.db` in the platform-local `clash-proxy-tester` application-data directory. Override the destination with `--run_history_path`; tests and development probes should always use a disposable path.

The full `main.py` pipeline and direct `python -m function.fix` / `python -m function.speed` commands use the same record format. A record contains:

- a UTC-derived run ID, process ID, argument vector, Git commit/dirty-tree state, and UTC start/finish timestamps;
- `origin`, resolved as `cron` when a Linux `cron`/`crond` ancestor is present and `manual` otherwise;
- elapsed wall time measured with a monotonic clock;
- terminal `success` or `failed` status and, for failures, the exception type and message;
- the selected profile path and metrics for each completed stage.

Stage metrics preserve the major funnel counts: merged proxies; unsupported, cooldown, redundant, and Mihomo-rejected removals; latency passes; speed `N/A` and below-retention host outcomes; cooldown outcomes; and final proxies. If a run fails, only stages completed before the error appear. The failed record is appended before the exception is re-raised.

Use `--run_origin cron` or `--run_origin manual` when a wrapper or scheduler hides the relevant process ancestry. `--run_origin auto` is the default.

Example:

```bash
python main.py \
  --mode meta \
  --run_origin cron \
  --run_history_path /path/to/state/run_history.jsonl
```

The file is append-only and protected with an exclusive file lock. A terminal record is flushed and synced before the command reports completion, so cron/manual comparisons do not depend on reconstructing outcomes from rotated prose logs.

For full-pipeline runs, a failed terminal record also means the selected profile was never replaced: intermediate mutations occur only in a sibling staging file, which is removed on failure. Completed stage metrics remain in history for diagnosis even though their staged profile mutations were not published.

Sensitive argument values are redacted before persistence. In particular, both `--controller_password VALUE` and `--controller_password=VALUE` retain the option name but store `[REDACTED]` instead of the secret. Locking uses `fcntl` on Unix and `msvcrt` on Windows through a sidecar `.lock` file; the lock file contains no run data.
