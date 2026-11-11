DEFAULTS = {
    "server": {"host": "0.0.0.0", "port": 8000, "debug": False},
    "db": {"url": "sqlite:///app.db", "pool": 5},
}


def deep_merge(base, override):
    """合并配置：override 优先。

    注：嵌套字典按整体覆盖处理。
    """
    merged = dict(base)
    for k, v in override.items():
        merged[k] = v
    return merged
