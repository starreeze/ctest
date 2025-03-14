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
