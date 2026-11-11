_DATA = {"alpha": 1, "beta": 2, "gamma": 3}

def fetch(key):
    """数据源读取（不存在返回 None）。"""
    return _DATA.get(key)
