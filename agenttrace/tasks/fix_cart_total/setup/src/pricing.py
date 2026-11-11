"""调用方：报表层按 config 口径校验订单总价。"""
from config import TAX_RATE, BOOK_DISCOUNT

def expected_total(prices, kinds=None):
    subtotal = sum(
        p * (1 - BOOK_DISCOUNT) if k == "book" else p
        for p, k in zip(prices, kinds or ["misc"] * len(prices))
    )
    return round(subtotal * (1 + TAX_RATE), 2)
