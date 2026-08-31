import base64
import contextlib
import os
import re
import shutil
import tempfile

import yaml


class FlowStyleDict(dict):
    """A dict that should be YAML-dumped in flow style (inline)"""

    pass


class ClashDumper(yaml.SafeDumper):
    pass


@contextlib.contextmanager
def staged_profile_update(profile_path: str):
    """Yield a sibling working copy and atomically publish it on success."""
    profile_dir = os.path.dirname(os.path.abspath(profile_path))
    basename, extension = os.path.splitext(os.path.basename(profile_path))
    fd, staged_path = tempfile.mkstemp(
        prefix=f".{basename}.staging-",
        suffix=extension,
        dir=profile_dir,
    )
    os.close(fd)
    try:
        shutil.copy2(profile_path, staged_path)
        yield staged_path
        os.replace(staged_path, profile_path)
    except BaseException:
        if os.path.exists(staged_path):
            os.unlink(staged_path)
        raise


def _represent_str(dumper: yaml.Dumper, data: str) -> yaml.Node:
    # Always quote: unquoted hex like 8e45 is a YAML 1.1 float and Mihomo then rejects the short-id.
    style = "'" if "'" not in data else '"'
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


def _represent_flow_dict(dumper: yaml.Dumper, data: FlowStyleDict) -> yaml.Node:
    return dumper.represent_mapping("tag:yaml.org,2002:map", data, flow_style=True)


ClashDumper.add_representer(str, _represent_str)
ClashDumper.add_representer(FlowStyleDict, _represent_flow_dict)


def quote_ipv6_server_addresses(yaml_content: str) -> str:
    """Quote IPv6 addresses in server fields"""

    def replacer(match: re.Match[str]) -> str:
        value = match.group(2)
        if ":" in value:
            return f'{match.group(1)} "{value}"'
        return match.group(0)

    return re.sub(r"(server:)\s+([0-9a-fA-F:]+)", replacer, yaml_content)


_JSDELIVR_GH = re.compile(
    r"^https://(?:cdn|fastly|gcore|testing)\.jsdelivr\.net/gh/([^/]+)/([^/@]+)@([^/]+)/(.*)$"
)
_RAW_GITHUB_REFS_HEADS = re.compile(
    r"^https://raw\.githubusercontent\.com/([^/]+)/([^/]+)/refs/heads/([^/]+)/(.*)$"
)
_RAW_GITHUB = re.compile(r"^https://raw\.githubusercontent\.com/([^/]+)/([^/]+)/([^/]+)/(.*)$")
_GITHUB_BLOB_OR_RAW = re.compile(
    r"^https://github\.com/([^/]+)/([^/]+)/(?:blob|raw)/(?:refs/heads/)?([^/]+)/(.*)$"
)


def github_feed_parts(url: str) -> tuple[str, str, str, str] | None:
    """Return (owner, repo, ref, path) for GitHub or jsDelivr gh/ feed URLs."""
    for pattern in (_JSDELIVR_GH, _RAW_GITHUB_REFS_HEADS, _GITHUB_BLOB_OR_RAW, _RAW_GITHUB):
        if match := pattern.match(url):
            return match.groups()
    return None


def rewrite_github_feed_url(url: str) -> str:
    """Send GitHub and jsDelivr feeds through fastly.jsdelivr.net so api.v1.mk can fetch them."""
    parts = github_feed_parts(url)
    if parts is None:
        return url
    owner, repo, ref, path = parts
    return f"https://fastly.jsdelivr.net/gh/{owner}/{repo}@{ref}/{path}"


_VLESS_X25519_PASSWORD_SIZE = 32
_VLESS_MLKEM768_CLIENT_LENGTH = 1184


def mihomo_accepts_vless_encryption(encryption: str) -> bool:
    """True if Mihomo NewClient would accept this VLESS encryption field."""
    if encryption in ("", "none"):
        return True
    parts = encryption.split(".")
    if len(parts) < 4 or parts[0] != "mlkem768x25519plus":
        return False
    if parts[1] not in {"native", "xorpub", "random"}:
        return False
    if parts[2] not in {"1rtt", "0rtt"}:
        return False
    keys = 0
    for token in parts[3:]:
        if len(token) < 20:
            continue
        pad = "=" * ((4 - len(token) % 4) % 4)
        try:
            raw = base64.urlsafe_b64decode(token + pad)
        except Exception:
            return False
        if len(raw) not in (_VLESS_X25519_PASSWORD_SIZE, _VLESS_MLKEM768_CLIENT_LENGTH):
            return False
        keys += 1
    return keys > 0


def decode_subconverter_body(raw: bytes) -> tuple[str, int]:
    """Decode converter YAML. Returns (text, replacement_count). Public backends sometimes emit invalid UTF-8."""
    try:
        return raw.decode("utf-8"), 0
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
        return text, text.count("\ufffd")


def load_raw_clash_yaml(content: str) -> dict:
    """Parse subconverter output into a clash profile dict. Reject HTML and empty results."""
    stripped = content.lstrip()
    if stripped.lower().startswith(("<!", "<html")):
        raise ValueError("subconverter returned HTML instead of YAML")
    data = yaml.safe_load(quote_ipv6_server_addresses(content.replace("!<str>", "!!str")))
    if not isinstance(data, dict) or not data.get("proxies"):
        raise ValueError("subconverter returned content without proxies")
    return data


def dump_yaml(data: dict) -> str:
    assert "proxies" in data, "proxies must be in data"
    data["proxies"] = [FlowStyleDict(proxy) for proxy in data["proxies"]]
    return yaml.dump(
        data,
        Dumper=ClashDumper,
        sort_keys=False,
        width=10**9,
        indent=2,
        allow_unicode=True,
        default_flow_style=False,
    )
