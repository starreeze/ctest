Clash utils for updating profiles, testing speed and fixing unsupported names

## Quick Start

### Install ClashVerge

Refer to https://github.com/clash-verge-rev/clash-verge-rev. There are lot of tutorials on this software so I may omit it here.

After installation, import remote subscription link (refer to the [sub-link](#sub-link) section). You may encounter errors but no need to worry - see the solution below.

### Install This CodeBase

```bash
python -m pip install -r requirements.txt
```

<!-- Then, modify `args.py:Config.profiles` to your profile path (multiple profiles supported). If you leave it empty, it will automatically use the profiles from clash-verge-rev.

You can also modify more arguments there. -->

## Usage

### Entire solution

```bash
python main.py
```

It will by default **overwrite** your latest profile using links specified in urls.txt. Keep `urls.txt` in raw GitHub form; GitHub raw, `github.com/.../blob|raw/`, and `cdn.jsdelivr.net` feeds are rewritten to `fastly.jsdelivr.net` before conversion so `api.v1.mk` can fetch them. Prefix a line with `!` to pin that feed's hosts: they are still tested, but they are not dropped for consecutive failures or a latency timeout. Conversion backends are tried in order (`api.v1.mk`, then bianyuan) until a valid clash YAML is returned; HTML/error pages are never written. Bianyuan is the legacy fallback and may drop VLESS/hy2/TUIC. Since some of the naming conventions are not recognized by clash meta, automatic fix will be done.

You need to manually reactivate your profile before pressing ENTER to run latency test (unless `--mode meta`). Speed test is enabled by default; pass `--test_speed False` to skip it.

After finishing, reactivate your profile again. The proxy names now have their latency and speed info on them: `{latency_ms} - {download_MiBps} - {original_name}`. They are sorted by downloading speed by default.

Each full or directly launched `fix`/`speed` run appends a compact JSONL summary after it succeeds or fails. See [Run history](docs/run-history.md) for the location, fields, and cron/manual origin controls.

Below are some separate functions for reference only and you may not need them.

### Fix unsupported names

```bash
python -m function.fix
```

After completion, reactivate your clash profile.

### Speed test

The speed test script latency-tests every host:port candidate. For each host it throughput-tests survivors in latency order and stops at the first endpoint with positive traffic, then adds latency and speed information to that winning endpoint's name.

Make sure that your clash profile is constructed by [subconverter](https://github.com/tindy2013/subconverter) which uses the [external config](https://github.com/tindy2013/subconverter/blob/master/README-cn.md#%E8%B0%83%E7%94%A8%E8%AF%B4%E6%98%8E-%E8%BF%9B%E9%98%B6) from https://fastly.jsdelivr.net/gh/starreeze/blogimage@main/subconverter/external.ini. You may also need to check the external controller port and the proxy mixed port in your clash settings. You can either modify the `args.py` or modify the settings upon difference.

If v1.mk later fails to emit inline Mihomo nodes, a possible replacement is: pull a nodelist from `api.asailor.org` with `list=true` (it parses VLESS/hy2/TUIC), then assign those proxies into groups locally. Do not use asailor's default `proxy-providers` output; this repo expects a `proxies:` list and the external.ini groups.

```bash
python -m function.speed
```

After completion, reactivate your clash profile.

Speed test defaults to `--speed_test_mode adaptive` (HTTPS download through the selected node). Pass `--speed_test_mode sdk` to use `speedtest-cli` instead.

```bash
python -m function.speed --speed_test_mode sdk
```

Adaptive mode uses `https://speed.cloudflare.com/__down?bytes={bytes}` by default. It ramps through 1, 4, 8, and 16 MiB probes until the response body takes at least three seconds, then collects two measurements at that size. Connection setup/TTFB is logged separately from body goodput. There is no minimum-speed floor by default: `--min_speed_threshold_kbps 0` gives every probe the bounded `--speed_http_max_transfer_seconds` wall-clock budget (default 30 s). Setting a positive floor restores size-based budgets of `--speed_http_connect_overhead + size / floor`, capped by the same maximum; for example, `--min_speed_threshold_kbps 512` produces **7 / 13 / 21 / 30 s** budgets for the default sizes. The stored score is:

```text
successful trial fraction * p25(successful body goodput in MiB/s)
```

If a larger ramp size fails after a smaller size succeeded, the smaller size is reused and the score is multiplied by `--speed_http_ramp_fail_factor` (default `0.85`). `--speed_test_retry` applies only to `sdk` mode; adaptive retries are `--speed_http_trials` at the selected size.

This penalizes flaky endpoints and avoids ranking a node by one lucky peak. Eight MiB is sufficient when its body transfer lasts roughly three seconds. Faster nodes automatically move to 16 MiB; slow nodes stop earlier. A long connection setup alone does not trigger a larger payload because it is measured as TTFB, not body-transfer time. With defaults, a node that stops at 8 MiB transfers at most 21 MiB across the ramp and trials; a node that reaches 16 MiB can transfer at most 45 MiB.

During throughput tests the core is switched to `global` mode and the node is selected on both `GLOBAL` and `--target_group`, then the previous mode is restored. That keeps mixed-port measurement traffic on the node being tested instead of following Clash rules.

In meta mode, the test core uses a temporary copy of the profile. Only the configured HTTP proxy and controller are bound on localhost; SOCKS, DNS, TUN, redirection, and transparent-proxy listeners are disabled so the test cannot claim unrelated local ports. Deprecated DNS `fallback-filter.geosite` routing is migrated to `nameserver-policy` while preserving the same fallback resolvers.

Candidates are deduplicated by host:port and all receive latency tests. For each host, latency-valid endpoints are speed-tested from lowest latency upward until one produces positive traffic; that winner represents the host in the final profile. Failed hosts use a 0/23/71/167-hour cooldown sequence, keyed by host: the first failure is retained but does not delay the next attempt, while the fourth and later failures remain capped at 167 hours. Any later success clears the streak. Cooldowns are anchored to the run start rather than the later result-write time; `--failure_cooldown_head_start_hours` defaults to one and is subtracted from each positive tier so a daily cron invocation can retry an expired host. In adaptive mode, speed-derived failure writes are skipped when the configured failure ratio indicates that the shared measurement endpoint itself is likely down.

`--speed_test_url` must use HTTPS. One HTTPS redirect is followed; an HTTP hop or a second redirect fails the probe. Requests send `Referer` set to the URL origin so Cloudflare `__down` accepts 16 MiB probes. The URL may contain a `{bytes}` placeholder, or point to a fixed object on a server that supports byte ranges. Prefer an endpoint you operate if repeatability matters; public endpoints add server and peering variability.

`--speed_http_read_timeout` is a per-chunk stall timeout, capped by the remaining probe budget so a dead 1 MiB download cannot sit for 30 s.

The main adaptive controls are:

```bash
--speed_http_sizes_mb 1 4 8 16
--speed_http_min_duration 3
--speed_http_trials 2
--speed_http_percentile 0.25
--speed_http_connect_overhead 5
--speed_http_max_transfer_seconds 30
--speed_http_read_timeout 30
--min_speed_threshold_kbps 0
--speed_http_ramp_fail_factor 0.85
--speed_outage_min_samples 5
```

## License

GPLv3

## Sub-link

Here provide an out-of-the-box subscription link for clash (note that this may not be up-to-date).

```
https://api.v1.mk/sub?target=clash&url=https%3A%2F%2Ffastly.jsdelivr.net%2Fgh%2Fstarreeze%2Fctest%40main%2Fassets%2Fvultr-deploy.yaml|https%3A%2F%2Ffastly.jsdelivr.net%2Fgh%2Fstarreeze%2Fctest%40main%2Fassets%2Fnodefiltrate.yaml|https%3A%2F%2Ffastly.jsdelivr.net%2Fgh%2Fripaojiedian%2Ffreenode%40main%2Fclash|https%3A%2F%2Ffastly.jsdelivr.net%2Fgh%2Fzhangkaiitugithub%2Fpasscro%40main%2Fspeednodes.yaml|https%3A%2F%2Ffastly.jsdelivr.net%2Fgh%2Fanaer%2FSub%40main%2Fclash.yaml|https%3A%2F%2Ffastly.jsdelivr.net%2Fgh%2FHuibq%2FTrojanLinks%40master%2Flinks%2Fvmess|https%3A%2F%2Ffastly.jsdelivr.net%2Fgh%2Fermaozi%2Fget_subscribe%40main%2Fsubscribe%2Fclash.yml|https%3A%2F%2Ftt.vg%2Ffreeclash|https%3A%2F%2Ffastly.jsdelivr.net%2Fgh%2Ffree18%2Fv2ray%40main%2Fc.yaml|https%3A%2F%2Ffastly.jsdelivr.net%2Fgh%2Fpeasoft%2FNoMoreWalls%40master%2Flist.meta.yml|https%3A%2F%2Ffastly.jsdelivr.net%2Fgh%2FBarabama%2FFreeNodes%40main%2Fnodes%2Fyudou66.yaml|https%3A%2F%2Ffastly.jsdelivr.net%2Fgh%2Fdongchengjie%2Fairport%40main%2Fsubs%2Fmerged%2Ftested_within.yaml|https%3A%2F%2Ffastly.jsdelivr.net%2Fgh%2FBarabama%2FFreeNodes%40main%2Fnodes%2Fblues.yaml|https%3A%2F%2Ffastly.jsdelivr.net%2Fgh%2Fa2470982985%2FgetNode%40main%2Fclash.yaml|https%3A%2F%2Ffastly.jsdelivr.net%2Fgh%2FBarabama%2FFreeNodes%40main%2Fnodes%2Fclashmeta.yaml|https%3A%2F%2Ffastly.jsdelivr.net%2Fgh%2FBarabama%2FFreeNodes%40main%2Fnodes%2Fndnode.yaml|https%3A%2F%2Ffastly.jsdelivr.net%2Fgh%2FJsnzkpg%2FJsnzkpg%40Jsnzkpg%2FJsnzkpg|https%3A%2F%2Ffastly.jsdelivr.net%2Fgh%2F0xRadikal%2FFree-v2ray-Configs%40main%2Fverified%2Fconfigs_base64.txt|https%3A%2F%2Ffastly.jsdelivr.net%2Fgh%2FMatinGhanbari%2Fv2ray-configs%40main%2Fsubscriptions%2Fv2ray%2Fsuper-sub.txt|https%3A%2F%2Ffastly.jsdelivr.net%2Fgh%2FAu1rxx%2Ffree-vpn-subscriptions%40main%2Foutput%2Fclash.yaml|https%3A%2F%2Ffastly.jsdelivr.net%2Fgh%2Fawesome-vpn%2Fawesome-vpn%40master%2Fclash.yaml|https%3A%2F%2Ffastly.jsdelivr.net%2Fgh%2FALIILAPRO%2Fv2rayNG-Config%40main%2Fsub.txt&config=https%3A%2F%2Ffastly.jsdelivr.net%2Fgh%2Fstarreeze%2Fblogimage%40main%2Fsubconverter%2Fexternal.ini&emoji=true
```

It consists of the following [free link collections](urls.txt) (raw GitHub lines rewritten to Fastly jsDelivr for this static example) and my custom config file. Date-token feeds such as mibei are expanded at `update()` time and are omitted from this static example. You can quote the urls and combine them manually to form the above subscription link.
