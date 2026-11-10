def average(values: list) -> float:
    """返回数值列表的平均值（存在缺陷：空列表会除零）。"""
    return sum(values) / len(values)
