Clash utils for updating profiles, testing speed and fixing unsupported names

## Quick Start

## Install ClashVerge

Refer to https://github.com/clash-verge-rev/clash-verge-rev. There are lot of tutorials on this software so I may omit it here.

After installation, import remote subscription link [here](#sub-link). You may encounter errors but no need to worry - see the solution below.

### Install This CodeBase

```bash
python -m pip install -r requirements.txt
```

<!-- Then, modify `args.py:Config.profiles` to your profile path (multiple profiles supported). If you leave it empty, it will automatically use the profiles from clash-verge-rev.

You can also modify more arguments there. -->

## Usage

### Entire solution

```bash
python run.py
```

It will by default **overwrite** your latest profile using links specified in urls.txt. Since some of the naming conventions are not recognized by clash meta, automatic fix will be done.

You need to manually reactivate your profile before pressing ENTER to run latency test. Specify `--test_speed` to run real speed test.

After finishing, reactivate your profile again. The proxy names now have their latency and speed info on them: `{latency}-{download_MBps}-{original_name}`. They are sorted by downloading speed by default.

Below are some separate functions for reference only and you may not need them.

### Fix unsupported names

```bash
python -m function.fix
```

After completion, reactivate your clash profile.

### Speed test

The speed test script will test the speed of all proxies in the profile, and add speed and latency information in the proxy names.

Make sure that your clash profile is constructed by [subconverter](https://github.com/tindy2013/subconverter) which uses the [external config](https://github.com/tindy2013/subconverter/blob/master/README-cn.md#%E8%B0%83%E7%94%A8%E8%AF%B4%E6%98%8E-%E8%BF%9B%E9%98%B6) from https://raw.githubusercontent.com/starreeze/blogimage/main/subconverter/external.ini. You may also need to check the external controller port and the proxy mixed port in your clash settings. You can either modify the `args.py` or modify the settings upon difference.

```bash
python -m function.speed
```

After completion, reactivate your clash profile.

## License

GPLv3

## Sub-link

Here provide an [out-of-the-box subscription link](https://api.dler.io/sub?target=clash&url=https%3A%2F%2Fgitlab.com%2Fcolloq168%2Fnodefiltrate%2F-%2Fraw%2Fmain%2Ffiltrate%3Fref_type%3Dheads%7Chttps%3A%2F%2Fraw.githubusercontent.com%2Fripaojiedian%2Ffreenode%2Fmain%2Fsub%7Chttps%3A%2F%2Fraw.githubusercontent.com%2Fripaojiedian%2Ffreenode%2Fmain%2Fclash%7Chttps%3A%2F%2Fraw.githubusercontent.com%2Fzhangkaiitugithub%2Fpasscro%2Fmain%2Fspeednodes.yaml%7Chttps%3A%2F%2Fraw.githubusercontent.com%2Ffreefq%2Ffree%2Fmaster%2Fv2%7Chttps%3A%2F%2Fraw.githubusercontent.com%2Fanaer%2FSub%2Fmain%2Fclash.yaml%7Chttps%3A%2F%2Fraw.githubusercontent.com%2FHuibq%2FTrojanLinks%2Fmaster%2Flinks%2Fvmess%7Chttps%3A%2F%2Fraw.githubusercontent.com%2Fqjlxg%2Fhy2%2Fmain%2Fsplitted%2Fhy2%7Chttps%3A%2F%2Fraw.githubusercontent.com%2Faiboboxx%2Fv2rayfree%2Fmain%2Fv2&config=https%3A%2F%2Fraw.githubusercontent.com%2Fstarreeze%2Fblogimage%2Fmain%2Fsubconverter%2Fexternal.ini&emoji=true) for clash (note that this may not be up-to-date).

It consists of the following [free link collections](urls.txt) and my custom config file. You can quote the urls and combine them manually to form the above subscription link.
