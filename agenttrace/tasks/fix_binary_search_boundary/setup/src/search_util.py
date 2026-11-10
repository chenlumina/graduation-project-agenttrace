def binary_search(arr: list, target) -> int:
    """二分查找（存在缺陷：循环条件漏判边界元素）。"""
    low, high = 0, len(arr) - 1
    while low < high:
        mid = (low + high) // 2
        if arr[mid] == target:
            return mid
        if arr[mid] < target:
            low = mid + 1
        else:
            high = mid - 1
    return -1
