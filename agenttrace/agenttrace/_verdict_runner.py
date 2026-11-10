"""判定子进程：隐藏测试在**内存中**执行，且被测代码无法改写测试框架。

为什么需要这个独立进程（安全模型 v4）：

- 隐藏测试源码只经 **stdin** 传给本进程，**不落盘**——被测代码无法用相对路径
  或绝对路径读到测试内容（原先测试文件与被测代码同处一个工作目录，可被读取并
  经 stdout 回灌给模型）。
- 运行前对 `unittest` 模块命名空间与 `TestCase` 类做**快照**，运行后比对：
  被测代码在 import 期或测试期改写 `unittest.TestCase.assertXxx` 会被检出。
- 额外安装 **审计钩子**，直接阻断对 unittest 对象的属性改写（audit hook 一旦
  安装无法被用户代码移除）。
- 结果以**带随机 nonce 的哨兵文件**回报（nonce 只经 stdin 传递，被测代码读不到），
  父进程据此 fail-closed 地判定成败。

输入（stdin，JSON）：
    {"nonce": "...", "sentinel_path": "...", "package": "hidden_tests",
     "files": [{"name": "test_x.py", "source": "..."}]}
"""
from __future__ import annotations

import json
import sys
import types
import unittest
import unittest.case


def _snapshot() -> dict:
    """记录 unittest 相关命名空间的对象身份，用于事后检测篡改。"""
    snap: dict[str, dict[str, int]] = {}
    for name, mod in (("unittest", unittest), ("unittest.case", unittest.case)):
        snap[name] = {k: id(v) for k, v in vars(mod).items()}
    snap["TestCase"] = {k: id(v) for k, v in vars(unittest.TestCase).items()}
    snap["TestLoader"] = {k: id(v) for k, v in vars(unittest.TestLoader).items()}
    snap["TestResult"] = {k: id(v) for k, v in vars(unittest.TestResult).items()}
    return snap


def _install_guard() -> None:
    """阻断对 unittest 框架对象的属性改写（审计钩子不可卸载）。"""
    protected = {
        unittest,
        unittest.case,
        unittest.TestCase,
        unittest.TestLoader,
        unittest.TestResult,
        unittest.TestSuite,
        unittest.TextTestRunner,
    }

    def hook(event: str, args: tuple) -> None:
        if event != "object.__setattr__" or not args:
            return
        target = args[0]
        if target in protected:
            raise RuntimeError("判定环境禁止修改测试框架（unittest）")

    sys.addaudithook(hook)


def _load_modules(files: list[dict], package: str) -> list[types.ModuleType]:
    """把隐藏测试源码在内存中编译成模块（不落盘）。"""
    pkg = types.ModuleType(package)
    pkg.__path__ = []  # 标记为包，使 `from pkg.mod import X` 形式可用
    sys.modules[package] = pkg
    modules: list[types.ModuleType] = []
    for item in files:
        name = item["name"]
        mod_name = f"{package}.{name[:-3] if name.endswith('.py') else name}"
        mod = types.ModuleType(mod_name)
        mod.__file__ = f"<hidden_tests>/{name}"
        mod.__package__ = package
        sys.modules[mod_name] = mod
        exec(compile(item["source"], mod.__file__, "exec"), mod.__dict__)  # noqa: S102
        setattr(pkg, mod_name.rsplit(".", 1)[-1], mod)
        modules.append(mod)
    return modules


def _write_sentinel(path: str, nonce: str, ran: int, tampered: bool, ok: bool) -> None:
    payload = f"{nonce}|ran={ran}|tamper={'yes' if tampered else 'no'}|ok={'yes' if ok else 'no'}"
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(payload)
    except OSError:
        pass


def main() -> int:
    try:
        cfg = json.loads(sys.stdin.read())
    except Exception as e:  # pragma: no cover - 协议异常直接失败
        sys.stderr.write(f"判定进程配置解析失败: {e}\n")
        return 2

    nonce = str(cfg.get("nonce", ""))
    sentinel_path = str(cfg.get("sentinel_path", ""))
    package = str(cfg.get("package", "hidden_tests"))
    files = list(cfg.get("files") or [])

    # 被测产物目录由父进程经 payload 传入并插入 sys.path：隐藏测试里的
    # `from src.xxx import …` 依赖它。不依赖 PYTHONPATH（部分发行版解释器
    # 内建 -E，会忽略该变量，M11 后在本机实测必现）。
    ws_dir = str(cfg.get("ws_dir", ""))
    if ws_dir and ws_dir not in sys.path:
        sys.path.insert(0, ws_dir)

    _install_guard()
    before = _snapshot()

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    try:
        modules = _load_modules(files, package)
        for mod in modules:
            suite.addTests(loader.loadTestsFromModule(mod))
    except Exception as e:
        sys.stderr.write(f"隐藏测试加载失败: {type(e).__name__}: {e}\n")
        _write_sentinel(sentinel_path, nonce, 0, False, False)
        return 1

    result = unittest.TextTestRunner(verbosity=1).run(suite)
    after = _snapshot()
    tampered = before != after
    ran = int(result.testsRun)
    ok = bool(result.wasSuccessful()) and ran > 0 and not tampered

    _write_sentinel(sentinel_path, nonce, ran, tampered, ok)
    # 哨兵文件可能被被测代码的 atexit 覆盖，父进程因此以 fail-closed 方式核对
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
