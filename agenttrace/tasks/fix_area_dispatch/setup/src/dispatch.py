"""按形状汇总面积。调用方语义：area 总是返回图形的面积。"""
from shapes import circle, square

def get_total_area(items):
    """items: [(shape, value), ...] -> 全部图形面积之和。"""
    total = 0.0
    for shape, value in items:
        if shape == "circle":
            total += circle.area(value)
        elif shape == "square":
            total += square.area(value)
        else:
            raise ValueError(f"未知形状: {shape}")
    return total
