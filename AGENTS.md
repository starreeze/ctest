# Project Guide

## Purpose and pipeline

This repository builds a usable Clash/Mihomo profile from public proxy feeds. The normal pipeline is:

1. `function/update.py` reads enabled, non-comment lines from `urls.txt`, expands any `strftime` date tokens, rewrites GitHub/`cdn.jsdelivr.net` feeds to `fastly.jsdelivr.net`, URL-encodes the feeds, and asks a subconverter backend for a merged Clash profile. Default backend is 肥羊 `api.v1.mk`; fallback is `pub-api-1.bianyuan.xyz`, which keeps a valid Clash YAML but drops Mihomo types. Lines prefixed with `keep ` or `keep:` are still converted and tested, but their hosts are pinned through later filtering.
2. `function/fix.py` removes unsupported or repeatedly failing endpoints, deduplicates by host, resolves duplicate names, repairs group references, and creates bounded test groups.
3. `function/speed.py` asks the Clash/Mihomo controller to measure reachability/latency, measures selected endpoints (adaptive HTTPS by default; `--speed_test_mode sdk` for speedtest-cli), records failures in SQLite, renames nodes as `latency - speed - original_name`, sorts them, and rewrites proxy groups.
4. `main.py` orchestrates the full workflow and may start/stop a local Mihomo process in `--mode meta`.

The scripts mutate the newest configured profile in place. Treat profile writes and the failure database as stateful operations, not harmless test fixtures.

## Repository map

- `common/args.py`: dataclass-backed CLI configuration, platform-specific profile discovery, logging, and runtime proxy environment setup. Importing it has side effects and expects the profile directory to exist unless `--profiles` is supplied.
- `common/api.py`: Clash/Mihomo controller calls plus latency and throughput measurement.
- `common/db.py`: persistent failure tracking keyed by host (`server`). Older `(server, port)` databases are collapsed to one row per host on open.
- `common/utils.py`: strict Clash YAML loading and custom YAML emission.
- `common/feeds.py`: `urls.txt` parsing, GitHub→jsDelivr rewrite helpers, and keep-feed host loading.
- `function/update.py`: feed aggregation and subconverter fallback.
- `function/fix.py`: endpoint filtering, deduplication, renaming, and group reconstruction.
- `function/speed.py`: measurement orchestration and final profile rewrite.
- `urls.txt`: one feed per line using raw GitHub URLs where possible (`raw.githubusercontent.com/...`). GitHub/jsDelivr lines are rewritten to `fastly.jsdelivr.net` at update time. Blank lines and `#` comments are ignored. Prefix a line with `keep ` or `keep:` to pin every host from that feed: it is still merged and tested, but `fix` will not drop it for consecutive failures and `speed` will not discard it after a latency timeout or record it in the failure database.
- `assets/nodefiltrate.yaml`: small repository-owned seed feed.
- `assets/vultr-deploy.yaml`: self-hosted endpoint, listed in `urls.txt` with `keep`.

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
- Preserve the `urls.txt` contract: blank lines and `#` comments are ignored; `%Y`, `%m`, `%d`, and other date tokens are expanded at runtime. Keep the list in raw GitHub form (`raw.githubusercontent.com` or `github.com/.../blob|raw/...`); GitHub raw / `cdn.jsdelivr.net` lines are rewritten at update time even if a new feed is pasted as `raw.githubusercontent.com`. Prefix self-hosted or otherwise pinned feeds with `keep ` so their hosts survive failure filtering and latency discard. Comment out a feed if `api.v1.mk` returns 403/400 for it: one blocked URL in the merged `url=` list makes the whole conversion 403. Do not re-enable commented feeds without new evidence that they consistently produce usable endpoints. Drop VLESS nodes whose `encryption` string is not `none`/empty and does not match Mihomo's `mlkem768x25519plus` client format (wrong key length fatal-exits the whole core).
- Before adding a feed, verify that its raw URL is stable, returns subscription or Clash-compatible content rather than an HTML/error page, materially adds unique endpoint identities after the full merge, and yields endpoints accepted by the configured subconverter and Mihomo. GitHub raw and `cdn.jsdelivr.net/gh/` URLs are rewritten to `fastly.jsdelivr.net/gh/` because `api.v1.mk` returns nginx 403 on `raw.githubusercontent.com` and `cdn.jsdelivr.net` but will fetch the Fastly/Gcore jsDelivr hosts and keep VLESS/hy2.
- Never write unvalidated converter output to a live profile. Keep `load_raw_clash_yaml()` validation and the temporary-file-plus-`os.replace()` update pattern. Skip a subconverter backend that returns only legacy Clash types (`ss`/`vmess`/`trojan`/...) when a later backend still emits Mihomo types (`vless`, `hysteria2`, `tuic`, ...). `target=clash` is not enough: some public backends silently drop those nodes. If the HTTP body is otherwise valid YAML but not strict UTF-8, replace invalid sequences rather than aborting the merge.
- If v1.mk later stops converting jsDelivr feeds into inline `proxies`, do not switch the live path to `api.asailor.org` default mode (that emits `proxy-providers` and this pipeline requires named `proxies` plus `external.ini` groups). A future replacement is: fetch a nodelist from `https://api.asailor.org/sub?target=clash&list=true&url=...` (server-side parse, keeps Mihomo types), then locally wrap those proxies into groups using the existing external config / `function/fix.py` group reconstruction.
- Maintain endpoint identity semantics across filtering and renaming: persistence/deduplication is keyed by host (`server`), while Clash group references are keyed by node name. Multiple ports on the same host are one endpoint.
- When changing proxy names or filtering logic, update every affected `proxy-groups[*].proxies` reference and test duplicate endpoint, duplicate name, unsupported field, IPv6 server, and empty-result cases.
- Keep the generated YAML acceptable to Clash/Mihomo: retain Unicode names, quote every string scalar (unquoted `8e45` is a YAML 1.1 float and fatal-exits as an invalid REALITY short-id), and emit proxy mappings in flow style. After writing, run `mihomo -t` and drop any remaining proxy that still fatal-exits the core.
- Controller requests to localhost must bypass the runtime proxy. External measurement traffic must traverse the selected node: speed tests switch the core to `global` mode, select the node on both `GLOBAL` and the configured target group, then restore the previous mode.
- Adaptive HTTPS measurement follows at most one HTTPS redirect, sends `Referer` from the test URL origin, reads exactly the requested number of body bytes, and treats a failed larger ramp size as a fallback to the last successful size with `--speed_http_ramp_fail_factor`. Probe wall time is `--speed_http_connect_overhead` plus size / `--min_speed_threshold_kbps`, capped by `--speed_http_max_transfer_seconds` (defaults 7/13/21/30 s for 1/4/8/16 MiB). `--speed_test_retry` is sdk-only; adaptive retries are `--speed_http_trials` (default 2). Throughput tests stop after `--max_num` nodes meet `--load_balance_thres`.
- Adaptive speed results of 0 (no successful sample) are persisted unless every tested node aborted (`tested >= --speed_outage_min_samples`). Completed-but-slow nodes are ranked in the profile and are not written to the failure database.
- Throughput results are noisy and consume third-party bandwidth. Prefer bounded transfers, multiple observations, robust aggregation, and explicit units; do not treat a single peak sample as durable endpoint quality.
- Treat `MetaLifecycle.stop()` as destructive process management: it runs `pkill -f mihomo` and can stop Mihomo processes unrelated to this repository. Do not exercise it on a shared host during routine validation.
- Do not commit generated profiles, the local SQLite failure database, downloaded test payloads, credentials, controller secrets, or subscription URLs containing private tokens.

## Validation expectations

- For pure transformation changes, add focused tests that use temporary YAML and SQLite paths rather than the user's live profile or application-data database.
- For feed changes, report the retrieval time, HTTP/content validation, converter acceptance, endpoint count before and after deduplication, and at least one Mihomo reachability check. A successful HTTP response alone is insufficient.
- For measurement changes, compare repeated runs and report median or percentile behavior, failure rate, transferred bytes, elapsed time, and units. Keep latency and throughput separate; one is not a substitute for the other.
- For full-pipeline tests, pass disposable `--profiles`, `--failure_db_path`, controller, and proxy settings. Confirm the resulting file parses and all proxy-group names resolve to an existing proxy or built-in policy.
