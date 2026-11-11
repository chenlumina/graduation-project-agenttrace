_mem = {}

def lookup(key):
    """查询：命中内存缓存直接返回。"""
    return _mem.get(key)


def put(key, value):
    _mem[key] = value


def mem_size():
    return len(_mem)
