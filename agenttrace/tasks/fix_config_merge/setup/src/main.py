"""调用方：用户只覆盖关心的键，其余沿用默认值。"""
from config import DEFAULTS, deep_merge

def load_config(user_cfg):
    """用户配置与默认配置合并：
    - 同名嵌套字典逐键合并（用户键覆盖，默认键保留）
    - 非字典值用户优先
    """
    return deep_merge(DEFAULTS, user_cfg)
