def is_leap_year(year: int) -> bool:
    """判断闰年（存在缺陷：未正确处理整百年份）。"""
    return year % 4 == 0
