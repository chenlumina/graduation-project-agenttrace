def is_palindrome(s: str) -> bool:
    """判断回文（存在缺陷：未按约定忽略大小写与空白）。"""
    return s == s[::-1]
