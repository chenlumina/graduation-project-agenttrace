def flatten(xs: list) -> list:
    """展平嵌套列表（存在缺陷：只展开一层）。"""
    out: list = []
    for x in xs:
        if isinstance(x, list):
            out.extend(x)
        else:
            out.append(x)
    return out
