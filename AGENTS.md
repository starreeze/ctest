# Project Guide

## Purpose and pipeline

This repository builds a usable Clash/Mihomo profile from public proxy feeds. The normal pipeline is:

1. `function/update.py` reads enabled, non-comment lines from `urls.txt`, expands any `strftime` date tokens, URL-encodes the feeds, and asks one of the configured subconverter backends for a merged Clash profile.
2. `function/fix.py` removes unsupported or repeatedly failing endpoints, deduplicates `server:port`, resolves duplicate names, repairs group references, and creates bounded test groups.
3. `function/speed.py` asks the Clash/Mihomo controller to measure reachability/latency, measures selected endpoints, records failures in SQLite, renames nodes as `latency - speed - original_name`, sorts them, and rewrites proxy groups.
4. `main.py` orchestrates the full workflow and may start/stop a local Mihomo process in `--mode meta`.

The scripts mutate the newest configured profile in place. Treat profile writes and the failure database as stateful operations, not harmless test fixtures.

## Repository map

- `common/args.py`: dataclass-backed CLI configuration, platform-specific profile discovery, logging, and runtime proxy environment setup. Importing it has side effects and expects the profile directory to exist unless `--profiles` is supplied.
- `common/api.py`: Clash/Mihomo controller calls plus latency and throughput measurement.
- `common/db.py`: persistent failure tracking keyed by `server, port`.
- `common/utils.py`: strict Clash YAML loading and custom YAML emission.
- `function/update.py`: feed aggregation and subconverter fallback.
- `function/fix.py`: endpoint filtering, deduplication, renaming, and group reconstruction.
- `function/speed.py`: measurement orchestration and final profile rewrite.
- `urls.txt`: one feed per line. Lines beginning with `#` are deliberately disabled because they have persistently yielded no usable endpoints.
- `assets/nodefiltrate.yaml`: small repository-owned seed feed.

## Commands

Use Python 3.10 or newer; the codebase uses PEP 604 union annotations and other modern typing syntax.

Install dependencies with:

```bash
python -m pip install -r requirements.txt
```

Run the complete pipeline from the repository root with:

```bash
python main.py
```

Run individual mutation stages with:

```bash
python -m function.fix
python -m function.speed
```

Run the focused unit tests with:

```bash
python -m unittest discover -s tests -v
```

There is no configured formatter or linter. For a low-risk syntax check that does not contact feeds or rewrite a profile, use:

```bash
python -m compileall -q main.py common function
```

Remove generated `__pycache__` directories after ad hoc checks. End-to-end validation requires a disposable Clash/Mihomo profile, a reachable controller, and explicit non-production paths/ports.

## Change constraints

- Keep CLI-tunable values in the `Config` or `TestArgs` dataclasses. Do not duplicate controller URLs, ports, thresholds, group names, or timeouts in implementation code.
- Preserve the `urls.txt` contract: blank lines and `#` comments are ignored; `%Y`, `%m`, `%d`, and other date tokens are expanded at runtime. Do not re-enable commented feeds without new evidence that they consistently produce usable endpoints.
- Before adding a feed, verify that its raw URL is stable, returns subscription or Clash-compatible content rather than an HTML/error page, materially adds unique endpoint identities after the full merge, and yields endpoints accepted by the configured subconverter and Mihomo.
- Never write unvalidated converter output to a live profile. Keep `load_raw_clash_yaml()` validation and the temporary-file-plus-`os.replace()` update pattern.
- Maintain endpoint identity semantics across filtering and renaming: persistence/deduplication is keyed by `server:port`, while Clash group references are keyed by node name.
- When changing proxy names or filtering logic, update every affected `proxy-groups[*].proxies` reference and test duplicate endpoint, duplicate name, unsupported field, IPv6 server, and empty-result cases.
- Keep the generated YAML acceptable to Clash/Mihomo: retain Unicode names, quote YAML-ambiguous strings, and emit proxy mappings in flow style.
- Controller requests to localhost must bypass the runtime proxy. External measurement traffic must traverse the selected node.
- Throughput results are noisy and consume third-party bandwidth. Prefer bounded transfers, multiple observations, robust aggregation, and explicit units; do not treat a single peak sample as durable endpoint quality.
- Treat `MetaLifecycle.stop()` as destructive process management: it runs `pkill -f mihomo` and can stop Mihomo processes unrelated to this repository. Do not exercise it on a shared host during routine validation.
- Do not commit generated profiles, the local SQLite failure database, downloaded test payloads, credentials, controller secrets, or subscription URLs containing private tokens.

## Validation expectations

- For pure transformation changes, add focused tests that use temporary YAML and SQLite paths rather than the user's live profile or application-data database.
- For feed changes, report the retrieval time, HTTP/content validation, converter acceptance, endpoint count before and after deduplication, and at least one Mihomo reachability check. A successful HTTP response alone is insufficient.
- For measurement changes, compare repeated runs and report median or percentile behavior, failure rate, transferred bytes, elapsed time, and units. Keep latency and throughput separate; one is not a substitute for the other.
- For full-pipeline tests, pass disposable `--profiles`, `--failure_db_path`, controller, and proxy settings. Confirm the resulting file parses and all proxy-group names resolve to an existing proxy or built-in policy.
