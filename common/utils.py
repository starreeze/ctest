import yaml


class FlowStyleDict(dict):
    """A dict that should be YAML-dumped in flow style (inline)"""

    pass


def flow_style_dict_representer(dumper, data):
    return dumper.represent_mapping("tag:yaml.org,2002:map", data, flow_style=True)


# Register the custom representer
yaml.add_representer(FlowStyleDict, flow_style_dict_representer)


def dump_yaml(data: dict) -> str:
    assert "proxies" in data, "proxies must be in data"
    data["proxies"] = [FlowStyleDict(proxy) for proxy in data["proxies"]]
    return yaml.dump(
        data,
        sort_keys=False,
        width=float("inf"),  # Prevent line breaks within flow dicts
        indent=2,
        allow_unicode=True,
        default_flow_style=False,  # Important: keep normal block style for containers
    )
