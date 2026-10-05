"""数据存取层：负责加载 / 保存看板数据。

优先级：items.json（流水线产出）> mock_items.json（内置示例数据）。
云托管平台的文件系统通常是临时的，因此示例数据模式下以只读为主。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .models import FeedbackIn, TrendItem

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent / "data"
ITEMS_FILE = DATA_DIR / "items.json"
MOCK_FILE = DATA_DIR / "mock_items.json"
FEEDBACK_FILE = DATA_DIR / "feedback.jsonl"


def _read_json(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("读取 %s 失败：%s", path.name, exc)
        return []


def load_items() -> list[TrendItem]:
    """加载看板数据，逐条校验；单条损坏只跳过该条，不影响整体。"""
    raw = _read_json(ITEMS_FILE) or _read_json(MOCK_FILE)
    items: list[TrendItem] = []
    for entry in raw:
        try:
            items.append(TrendItem.model_validate(entry))
        except Exception as exc:  # noqa: BLE001 - 单条数据不应拖垮整体
            logger.warning("跳过非法数据 %s：%s", entry.get("id", "?"), exc)
    return items


def save_items(items: list[TrendItem]) -> None:
    """保存流水线产出到 items.json。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = [item.model_dump(mode="json") for item in items]
    with ITEMS_FILE.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def append_feedback(feedback: FeedbackIn) -> None:
    """追加用户反馈，用于后续质量优化。写入失败不阻塞请求。"""
    record = feedback.model_dump(mode="json")
    record["created_at"] = datetime.now(timezone.utc).isoformat()
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with FEEDBACK_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning("反馈写入失败：%s", exc)