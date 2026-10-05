"""FastAPI 入口：提供看板 API 并托管前端静态页面。

启动：uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from . import github_client, llm, pipeline, reports
from .models import FeedbackIn, TrendItem
from .store import append_feedback, load_items

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="AI 趋势雷达 API", version="1.0.0")

_cache: dict[str, object] = {"items": None, "loaded_at": None}


def get_items(refresh: bool = False) -> list[TrendItem]:
    """内存缓存，避免每次请求都读盘。"""
    if refresh or _cache["items"] is None:
        _cache["items"] = load_items()
        _cache["loaded_at"] = datetime.now(timezone.utc)
    return _cache["items"]  # type: ignore[return-value]


@app.get("/healthz")
def healthz() -> dict:
    """云托管平台健康检查。"""
    return {"ok": True}


@app.get("/api/items")
def list_items() -> dict:
    """返回全部看板数据。筛选与排序在前端完成，以降低后端复杂度。"""
    items = get_items()
    return {
        "total": len(items),
        "items": [item.model_dump(mode="json") for item in items],
    }


@app.get("/api/meta")
def meta() -> dict:
    """看板元信息：统计、数据更新时间、能力开关。"""
    items = get_items()
    domains = Counter(item.domain for item in items)
    tags = Counter(tag for item in items for tag in item.tags)
    statuses = Counter(item.extraction.status.value for item in items)
    updated = max(
        (item.last_updated for item in items if item.last_updated),
        default=None,
    )

    # 两级分类：各知识领域下的热门技术关键词
    domain_tags: dict[str, Counter] = {}
    for item in items:
        domain_tags.setdefault(item.domain, Counter()).update(item.tags)

    return {
        "total": len(items),
        "last_updated": updated.isoformat() if updated else None,
        "generated_at": _cache["loaded_at"].isoformat() if _cache["loaded_at"] else None,  # type: ignore[union-attr]
        "domains": domains.most_common(),
        "domain_tags": {d: c.most_common(6) for d, c in domain_tags.items()},
        "top_tags": tags.most_common(12),
        "statuses": dict(statuses),
        "capabilities": {
            "llm_enabled": llm.is_enabled(),
            "github_enabled": github_client.is_enabled(),
        },
    }


@app.get("/api/items/{item_id:path}")
def get_item(item_id: str) -> dict:
    """获取单条内容详情。"""
    for item in get_items():
        if item.id == item_id:
            return item.model_dump(mode="json")
    raise HTTPException(status_code=404, detail="未找到该内容。")


@app.get("/api/analysis/{item_id:path}")
def analyze(item_id: str) -> dict:
    """第二层信息：生成/读取该项目的结构化总结报告。

    - 命中缓存（来源指纹一致）时直接返回，不重复调用大模型；
    - 未配置大模型时返回规则拼装的兜底报告，页面不会空白。
    """
    target = next((item for item in get_items() if item.id == item_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail="未找到该内容。")

    payload = target.model_dump(mode="json")
    readme = github_client.fetch_readme(target.full_name, max_chars=6000)

    report, from_cache = reports.get_or_create(payload, readme)
    return {
        "item": payload,
        "report": report.model_dump(mode="json"),
        "from_cache": from_cache,
        "llm_enabled": llm.is_enabled(),
    }


@app.get("/api/schema")
def schema() -> dict:
    """输出 TrendItem 的 JSON Schema，供前端或调试查看。"""
    return TrendItem.model_json_schema()


@app.post("/api/feedback")
def feedback(payload: FeedbackIn) -> dict:
    """记录用户对单条内容的有用/没用反馈。"""
    append_feedback(payload)
    return {"ok": True}


@app.post("/api/refresh")
def refresh() -> dict:
    """手动触发一次抓取与提取（需已配置 GITHUB_TOKEN）。"""
    if not github_client.is_enabled():
        raise HTTPException(status_code=503, detail="未配置 GITHUB_TOKEN，无法执行真实抓取。")
    items = pipeline.run_pipeline()
    get_items(refresh=True)
    return {"ok": True, "generated": len(items)}


# 静态资源挂载必须放在 API 路由之后，避免吞掉 /api 前缀
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")