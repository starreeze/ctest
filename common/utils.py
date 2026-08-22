import re

import yaml


class FlowStyleDict(dict):
    """A dict that should be YAML-dumped in flow style (inline)"""

    pass


class ClashDumper(yaml.SafeDumper):
    pass


_AMBIGUOUS_STR = re.compile(
    r"^(?:y|Y|yes|Yes|YES|n|N|no|No|NO|true|True|TRUE|false|False|FALSE|on|On|ON|off|Off|OFF|null|Null|NULL|~)$"
)


def _represent_str(dumper: yaml.Dumper, data: str) -> yaml.Node:
    needs_quote = (
        not data
        or data != data.strip()
        or _AMBIGUOUS_STR.match(data)
        or any(c in data for c in ":{}[],&*!#|>'\"%@`\n\t")
        or data[0] in "-?"
    )
    style = ""
    if needs_quote:
        # Single quotes keep emoji/unicode intact; yaml-cpp may not accept \U escapes.
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
