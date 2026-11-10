def sum_upto(n: int) -> int:
    """返回 1 到 n（含 n）的整数和（存在缺陷：漏掉 n 本身）。"""
    return sum(range(1, n))
