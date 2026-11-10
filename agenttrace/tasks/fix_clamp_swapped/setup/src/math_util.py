def clamp(value, lo, hi):
    """限制取值范围（存在缺陷：min/max 用反）。"""
    return min(lo, max(hi, value))
