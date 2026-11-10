def dedupe(xs: list) -> list:
    """去重（存在缺陷：排序破坏了首次出现顺序）。"""
    return sorted(set(xs))
