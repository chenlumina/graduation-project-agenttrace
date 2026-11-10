def count_words(text: str) -> dict:
    """统计单词频次（存在缺陷：未按约定忽略大小写）。"""
    counts: dict = {}
    for w in text.split():
        counts[w] = counts.get(w, 0) + 1
    return counts
