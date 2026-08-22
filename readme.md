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

It will by default **overwrite** your latest profile using links specified in urls.txt. Conversion backends are tried in order until a valid clash YAML is returned; HTML/error pages are never written. Since some of the naming conventions are not recognized by clash meta, automatic fix will be done.

You need to manually reactivate your profile before pressing ENTER to run latency test (unless `--mode meta`). Speed test is enabled by default; pass `--test_speed False` to skip it.

After finishing, reactivate your profile again. The proxy names now have their latency and speed info on them: `{latency_ms} - {download_MiBps} - {original_name}`. They are sorted by downloading speed by default.

Below are some separate functions for reference only and you may not need them.

### Fix unsupported names

```bash
python -m function.fix
```

After completion, reactivate your clash profile.

### Speed test

The speed test script latency-tests the profile's generated test groups. It then throughput-tests latency-sorted nodes until `--max_num` nodes meet the configured threshold, and adds latency and speed information to their names.

Make sure that your clash profile is constructed by [subconverter](https://github.com/tindy2013/subconverter) which uses the [external config](https://github.com/tindy2013/subconverter/blob/master/README-cn.md#%E8%B0%83%E7%94%A8%E8%AF%B4%E6%98%8E-%E8%BF%9B%E9%98%B6) from https://raw.githubusercontent.com/starreeze/blogimage/main/subconverter/external.ini. You may also need to check the external controller port and the proxy mixed port in your clash settings. You can either modify the `args.py` or modify the settings upon difference.

```bash
python -m function.speed
```

After completion, reactivate your clash profile.

The default `--speed_test_mode sdk` preserves the original `speedtest-cli` behavior. A standalone adaptive HTTPS implementation is also available:

```bash
python -m function.speed --speed_test_mode adaptive
```

Adaptive mode uses `https://speed.cloudflare.com/__down?bytes={bytes}` by default. It ramps through 1, 4, 8, 16, and 32 MiB probes until the response body takes at least three seconds, then collects three measurements at that size. Connection setup/TTFB is logged separately from body goodput. The stored score is:

```text
successful trial fraction * p25(successful body goodput in MiB/s)
```

This penalizes flaky endpoints and avoids ranking a node by one lucky peak. A failed ramp size is retried at that same size rather than silently falling back to an easier result. Low adaptive scores affect the current profile but are not persisted as proxy failures because a shared public measurement endpoint may itself be unhealthy.

Eight MiB is sufficient when its body transfer lasts roughly three seconds. Faster nodes automatically move to 16 or 32 MiB; slow nodes stop earlier. A long connection setup alone does not trigger a larger payload because it is measured as TTFB, not body-transfer time. With defaults, a node that stops at 8 MiB transfers at most 29 MiB across the ramp and trials; a node that reaches 32 MiB can transfer at most 125 MiB.

The main adaptive controls are:

```bash
--speed_http_sizes_mb 1 4 8 16 32
--speed_http_min_duration 3
--speed_http_trials 3
--speed_http_percentile 0.25
--speed_http_max_transfer_seconds 30
```

`--speed_test_url` must use HTTPS. It may contain a `{bytes}` placeholder, or point to a fixed object on a server that supports byte ranges. Prefer an endpoint you operate if repeatability matters; public endpoints add server and peering variability.

## License

GPLv3

## Sub-link

Here provide an out-of-the-box subscription link for clash (note that this may not be up-to-date).

```
https://api.dler.io/sub?target=clash&url=https%3A%2F%2Fgitlab.com%2Fcolloq168%2Fnodefiltrate%2F-%2Fraw%2Fmain%2Ffiltrate%3Fref_type%3Dheads%7Chttps%3A%2F%2Fraw.githubusercontent.com%2Fripaojiedian%2Ffreenode%2Fmain%2Fsub%7Chttps%3A%2F%2Fraw.githubusercontent.com%2Fripaojiedian%2Ffreenode%2Fmain%2Fclash%7Chttps%3A%2F%2Fraw.githubusercontent.com%2Fzhangkaiitugithub%2Fpasscro%2Fmain%2Fspeednodes.yaml%7Chttps%3A%2F%2Fraw.githubusercontent.com%2Ffreefq%2Ffree%2Fmaster%2Fv2%7Chttps%3A%2F%2Fraw.githubusercontent.com%2Fanaer%2FSub%2Fmain%2Fclash.yaml%7Chttps%3A%2F%2Fraw.githubusercontent.com%2FHuibq%2FTrojanLinks%2Fmaster%2Flinks%2Fvmess%7Chttps%3A%2F%2Fraw.githubusercontent.com%2Fqjlxg%2Fhy2%2Fmain%2Fsplitted%2Fhy2%7Chttps%3A%2F%2Fraw.githubusercontent.com%2Faiboboxx%2Fv2rayfree%2Fmain%2Fv2&config=https%3A%2F%2Fraw.githubusercontent.com%2Fstarreeze%2Fblogimage%2Fmain%2Fsubconverter%2Fexternal.ini&emoji=true
```

It consists of the following [free link collections](urls.txt) and my custom config file. You can quote the urls and combine them manually to form the above subscription link.
