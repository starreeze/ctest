Clash utils for testing speed and fixing unsupported cryptos

## Usage

### Install

```bash
python -m pip install -r requirements.txt
```

Then, modify `args.py:Config.profiles` to your profile path (multiple profiles supported). If you leave it empty, it will automatically use the profiles from clash-verge-rev.

You can also modify more arguments there.

### Fix unsupported cryptos

```bash
python fix.py
```

After completion, reactivate your clash profile.

### Speed test

The speed test script will test the speed of all proxies in the profile, and add speed and latency information in the proxy names.

Make sure that your clash profile is constructed by [subconverter](https://github.com/tindy2013/subconverter) which uses the [external config](https://github.com/tindy2013/subconverter/blob/master/README-cn.md#%E8%B0%83%E7%94%A8%E8%AF%B4%E6%98%8E-%E8%BF%9B%E9%98%B6) from https://raw.githubusercontent.com/starreeze/blogimage/main/subconverter/external.ini. You may also need to check the external controller port and the proxy mixed port in your clash settings. You can either modify the `args.py` or modify the settings upon difference.

```bash
python test_speed.py
```

After completion, reactivate your clash profile.

## License

GPLv3

## Sub-link

Here provide an out-of-the-box subscription link for clash:

```
https://api.dler.io/sub?target=clash&url=https%3A%2F%2Fgitlab.com%2Fcolloq168%2Fnodefiltrate%2F-%2Fraw%2Fmain%2Ffiltrate%3Fref_type%3Dheads%7Chttps%3A%2F%2Fraw.githubusercontent.com%2Fripaojiedian%2Ffreenode%2Fmain%2Fsub%7Chttps%3A%2F%2Fraw.githubusercontent.com%2Fripaojiedian%2Ffreenode%2Fmain%2Fclash%7Chttps%3A%2F%2Fraw.githubusercontent.com%2Fzhangkaiitugithub%2Fpasscro%2Fmain%2Fspeednodes.yaml%7Chttps%3A%2F%2Fraw.githubusercontent.com%2Ffreefq%2Ffree%2Fmaster%2Fv2%7Chttps%3A%2F%2Fraw.githubusercontent.com%2Fanaer%2FSub%2Fmain%2Fclash.yaml%7Chttps%3A%2F%2Fraw.githubusercontent.com%2FHuibq%2FTrojanLinks%2Fmaster%2Flinks%2Fvmess%7Chttps%3A%2F%2Fraw.githubusercontent.com%2Fqjlxg%2Fhy2%2Fmain%2Fsplitted%2Fhy2%7Chttps%3A%2F%2Fraw.githubusercontent.com%2Faiboboxx%2Fv2rayfree%2Fmain%2Fv2&config=https%3A%2F%2Fraw.githubusercontent.com%2Fstarreeze%2Fblogimage%2Fmain%2Fsubconverter%2Fexternal.ini&emoji=true
```

It consists of the following free link collections and my custom config file:

```
https://gitlab.com/colloq168/nodefiltrate/-/raw/main/filtrate?ref_type=heads
https://raw.githubusercontent.com/ripaojiedian/freenode/main/sub
https://raw.githubusercontent.com/ripaojiedian/freenode/main/clash
https://raw.githubusercontent.com/zhangkaiitugithub/passcro/main/speednodes.yaml
https://raw.githubusercontent.com/freefq/free/master/v2
https://raw.githubusercontent.com/anaer/Sub/main/clash.yaml
https://raw.githubusercontent.com/Huibq/TrojanLinks/master/links/vmess
https://raw.githubusercontent.com/qjlxg/hy2/main/splitted/hy2
https://raw.githubusercontent.com/aiboboxx/v2rayfree/main/v2
```
