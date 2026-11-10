def max_subarray_sum(arr: list) -> int:
    """最大子段和（存在缺陷：全负数组错误返回 0）。"""
    best = 0
    cur = 0
    for x in arr:
        cur = max(0, cur + x)
        best = max(best, cur)
    return best
