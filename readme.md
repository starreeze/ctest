Clash utils for testing speed and fixing unsupported cryptos

## Usage

### Install

```bash
python -m pip install -r requirements.txt
```

Then, modify `args.py:Config.profile_path` to your profile path. You can also modify more arguments there.

### Fix unsupported cryptos

```bash
python fix.py
```

After completion, reactivate your clash profile.

### Speed test

You may need to check the external controller port and the proxy mixed port in your clash settings. You can either modify the `args.py` or modify the settings upon difference. Then run:

```bash
python test_speed.py
```

After completion, reactivate your clash profile.

## License

GPLv3
