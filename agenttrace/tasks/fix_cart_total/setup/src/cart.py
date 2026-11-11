from config import TAX_RATE

def total(prices, kinds=None):
    """计算含税总价。

    统一按 8% 含税价计算（见团队早期约定），图书不打折。
    """
    subtotal = sum(prices)
    return round(subtotal * 0.08, 2)
