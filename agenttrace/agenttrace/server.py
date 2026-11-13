"""Web 服务：轨迹查询 API + 静态回放前端（FastAPI）。

路由：
- GET /                      → 回放页面（static/index.html）
- GET /static/*              → 前端静态资源
- GET /api/attempts          → 尝试列表
- GET /api/attempts/{id}     → 单次尝试完整轨迹（schema_version + attempt + events）
- GET /api/attempts/{id}/metrics → 两层指标与失败归因

安全约定（H-2 修复）：
- 轨迹接口需鉴权：设置 token（CLI --token / 环境变量 AGENTTRACE_API_TOKEN）后，
  请求必须带 X-AgentTrace-Token 头或 ?token= 查询参数，否则 401；
  未设置 token 时仅供本机回环调试使用；
- 所有响应附带安全头（nosniff / no-referrer / CSP / no-store 等）；
- attempt_id 必须符合 32 位十六进制格式，避免任意字符串进入查询路径。
"""
from __future__ import annotations

import hmac
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .metrics import classify, compute_metrics
from .store import TraceStore

PACKAGE_DIR = Path(__file__).resolve().parent
STATIC_DIR = PACKAGE_DIR / "static"

#: attempt_id 合法格式（uuid4().hex）
_ATTEMPT_ID_RE = re.compile(r"^[0-9a-f]{32}$")

#: 统一附加的安全响应头
SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' "
        "https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
        "base-uri 'none'; frame-ancestors 'none'"
    ),
}


def create_app(db_path: str | Path, token: str | None = None) -> FastAPI:
    app = FastAPI(title="AgentTrace", description="AI 编程 Agent 执行轨迹查看与回放")
    db = str(db_path)

    @app.middleware("http")
    async def auth_and_security_headers(request: Request, call_next):
        if token and request.url.path.startswith("/api/"):
            provided = request.headers.get("x-agenttrace-token") or request.query_params.get("token")
            # 用字节比较，避免非 ASCII 令牌触发 compare_digest 的类型限制
            if not provided or not hmac.compare_digest(str(provided).encode(), token.encode()):
                return JSONResponse(
                    {"detail": "unauthorized"}, status_code=401, headers=SECURITY_HEADERS
                )
        response = await call_next(request)
        for key, value in SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)
        return response

    def _check_attempt_id(attempt_id: str) -> None:
        if not _ATTEMPT_ID_RE.match(attempt_id):
            raise HTTPException(status_code=404, detail="attempt not found")

    @app.get("/api/attempts")
    def api_attempts(page: int = 1, page_size: int = 20, task_id: str | None = None):
        """R-19：分页列表（page/page_size，task_id 过滤）；派生字段（_ 前缀）不下发。"""
        page = max(1, page)
        page_size = max(1, min(page_size, 200))
        store = TraceStore(db)
        try:
            total = store.count_attempts(task_id)
            metas = store.list_attempts(
                limit=page_size, offset=(page - 1) * page_size, task_id=task_id
            )
            items = []
            for m in metas:
                d = m.to_dict()
                d["meta"] = {k: v for k, v in m.meta.items() if not k.startswith("_")}
                items.append(d)
            return {
                "attempts": items,
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": (total + page_size - 1) // page_size,
            }
        finally:
            store.close()

    @app.get("/api/attempts/{attempt_id}")
    def api_attempt(attempt_id: str, events_offset: int = 0, events_limit: int = 0):
        """R-19：支持轨迹分段取事件（events_limit=0 返回全量，兼容回放）。"""
        _check_attempt_id(attempt_id)
        store = TraceStore(db)
        try:
            doc = store.export(attempt_id)
            if events_limit > 0:
                start = max(0, events_offset)
                doc["events"] = doc["events"][start:start + events_limit]
                doc["events_offset"] = start
                doc["events_limit"] = events_limit
            doc["events_total"] = store.count_events(attempt_id)
            return doc
        except KeyError:
            raise HTTPException(status_code=404, detail="attempt not found")
        finally:
            store.close()

    @app.get("/api/attempts/{attempt_id}/metrics")
    def api_attempt_metrics(attempt_id: str):
        _check_attempt_id(attempt_id)
        store = TraceStore(db)
        try:
            meta = store.get_attempt(attempt_id)
            if meta is None:
                raise HTTPException(status_code=404, detail="attempt not found")
            # R-19：优先读预计算结果；缺失（旧库/未预计算）则现算并回写缓存
            cached_m = meta.meta.get("_metrics")
            cached_c = meta.meta.get("_classification")
            if cached_m is not None and cached_c is not None:
                return {
                    "attempt_id": attempt_id,
                    "metrics": cached_m,
                    "classification": cached_c,
                    "cached": True,
                }
            events = store.events(attempt_id)
            metrics = compute_metrics(events)
            classification = classify(events)
            store.update_attempt_meta(
                attempt_id, {"_metrics": metrics, "_classification": classification}
            )
            return {
                "attempt_id": attempt_id,
                "metrics": metrics,
                "classification": classification,
                "cached": False,
            }
        finally:
            store.close()

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    return app
