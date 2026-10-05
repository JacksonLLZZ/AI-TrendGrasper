"""总结报告缓存层。

按 item_id 缓存报告，并以「来源指纹」(source_hash) 判断是否需要重新生成，
避免重复调用大模型带来的延迟与成本。来源变化时才重新分析。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from . import llm
from .models import ExtractionStatus, Report, ReportSection

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent / "data"
CACHE_FILE = DATA_DIR / "reports_cache.json"

_cache: dict[str, dict] = {}
_loaded = False


def _load() -> None:
    global _cache, _loaded
    if _loaded:
        return
    _loaded = True
    if not CACHE_FILE.exists():
        return
    try:
        with CACHE_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            _cache = data
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("读取报告缓存失败：%s", exc)


def _save() -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with CACHE_FILE.open("w", encoding="utf-8") as f:
            json.dump(_cache, f, ensure_ascii=False, indent=2)
    except OSError as exc:
        logger.warning("写入报告缓存失败：%s", exc)


def source_hash(item: dict, readme: str) -> str:
    """来源指纹：README 变化或描述变化时，指纹随之改变。"""
    return llm.content_hash(
        item.get("full_name", ""),
        readme or "",
        item.get("description") or "",
        item.get("summary") or "",
    )


def get_or_create(item: dict, readme: str = "") -> tuple[Report, bool]:
    """获取报告；未命中缓存时调用大模型生成并写缓存。

    返回 (报告, 是否命中缓存)。
    """
    _load()
    item_id = item["id"]
    current_hash = source_hash(item, readme)

    cached = _cache.get(item_id)
    if cached and cached.get("source_hash") == current_hash:
        try:
            return Report.model_validate(cached["report"]), True
        except Exception as exc:  # noqa: BLE001 - 缓存损坏则重新生成
            logger.warning("报告缓存损坏，重新生成 %s：%s", item_id, exc)

    sections, status, model = llm.generate_report(item, readme)
    report = Report(
        item_id=item_id,
        title=item.get("name") or item_id,
        generated_at=datetime.now(timezone.utc),
        model=model,
        status=status,
        source_hash=current_hash,
        sections=[ReportSection.model_validate(s) for s in sections],
    )

    _cache[item_id] = {
        "source_hash": current_hash,
        "report": report.model_dump(mode="json"),
    }
    _save()
    return report, False