"""调用方：期望 lookup 体现『缓存优先、未命中回源 db.fetch 并写缓存』语义。"""
from cache import lookup, mem_size

def get(key):
    return lookup(key)
