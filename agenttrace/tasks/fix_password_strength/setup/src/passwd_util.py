def is_strong(pwd: str) -> bool:
    """强口令判断（存在缺陷：漏掉数字检查）。"""
    if len(pwd) < 8:
        return False
    has_lower = any(c.islower() for c in pwd)
    has_upper = any(c.isupper() for c in pwd)
    return has_lower and has_upper
