"""流水线编排：抓取 → LLM 结构化提取 → 合并 → 落盘。

设计原则：整条流水线永不因单个项目失败而中断，任何一个项目都会产出一条可渲染记录。
"""

from __future__ import annotations

import logging

from . import github_client, llm
from .models import TrendItem
from .store import save_items

logger = logging.getLogger(__name__)

# meta 中来自 GitHub API 的字段（与 AI 字段合并）
_META_FIELDS = (
    "id", "source", "full_name", "name", "url", "homepage", "description",
    "language", "license", "topics", "stars", "forks", "stars_delta_7d",
    "last_updated", "has_readme",
)


def build_item(meta: dict) -> TrendItem:
    """把单条元数据 + AI 字段合并成完整的 TrendItem。"""
    readme = github_client.fetch_readme(meta["full_name"]) if meta.get("has_readme") else ""
    meta = {**meta, "has_readme": bool(readme)}
    fields, info = llm.extract(meta, readme)
    payload = {k: meta.get(k) for k in _META_FIELDS if k in meta}
    payload["extraction"] = info
    payload.update(fields)
    return TrendItem.model_validate(payload)


def run_pipeline(limit_per_query: int = 15) -> list[TrendItem]:
    """执行一次完整抓取与提取。未配置凭证时返回空列表（不影响示例数据模式）。"""
    metas = github_client.fetch_repos(limit_per_query=limit_per_query)
    if not metas:
        logger.info("未获取到新数据（可能未配置 GITHUB_TOKEN）。")
        return []

    items: list[TrendItem] = []
    for meta in metas:
        try:
            items.append(build_item(meta))
        except Exception as exc:  # noqa: BLE001 - 单条失败不阻断整批
            logger.warning("构建数据失败 %s：%s", meta.get("full_name"), exc)

    if items:
        save_items(items)
    logger.info("流水线完成，共生成 %d 条数据。", len(items))
    return items