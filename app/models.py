"""数据模型：对齐 AI 结构化提取阶段定义的 TrendItem JSON Schema。

大模型只负责生成 AI 字段（summary / highlights / category / tags / maturity /
audience / product_use_case / trend），其余元数据来自 GitHub API，由后端合并成
最终喂给前端看板的完整对象。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# 知识领域（一级分类）：可拓展。
# 下列词表仅作为分类建议，不限制新增领域——大模型可自由给出新领域，便于后续扩展。
SUGGESTED_DOMAINS = [
    "大模型基础",
    "Agent 与自动化",
    "检索与知识",
    "多模态生成",
    "语音与音频",
    "推理与部署",
    "训练与微调",
    "数据与向量",
    "自动驾驶",
    "机器人 / 具身智能",
    "医疗健康",
    "金融科技",
    "教育科研",
    "企业应用",
    "开发者工具",
    "其他",
]


class Maturity(StrEnum):
    """成熟度。"""

    PRODUCTION = "production"
    USABLE = "usable"
    EXPERIMENTAL = "experimental"
    UNKNOWN = "unknown"


class Audience(StrEnum):
    """适用人群。"""

    TECHNICAL = "technical"
    PRODUCT = "product"


class Trend(StrEnum):
    """趋势方向。"""

    RISING = "rising"
    STABLE = "stable"
    COOLING = "cooling"
    UNKNOWN = "unknown"


class ExtractionStatus(StrEnum):
    """提取状态：成功 / 部分可用 / 规则兜底。"""

    SUCCESS = "success"
    PARTIAL = "partial"
    FALLBACK = "fallback"


class ExtractionInfo(BaseModel):
    """提取过程元信息。"""

    model_config = ConfigDict(protected_namespaces=())

    status: ExtractionStatus = ExtractionStatus.FALLBACK
    model: str = "rule-based"
    generated_at: datetime
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    retries: int = Field(default=0, ge=0)


class TrendItem(BaseModel):
    """看板单条内容（最终喂给前端的结构）。"""

    model_config = ConfigDict(protected_namespaces=())

    id: str
    source: Literal["github"] = "github"
    full_name: str
    name: str
    url: str
    homepage: str | None = None
    description: str | None = None
    language: str | None = None
    license: str | None = None
    topics: list[str] = Field(default_factory=list)

    stars: int = Field(default=0, ge=0)
    forks: int = Field(default=0, ge=0)
    stars_delta_7d: int = 0
    last_updated: datetime | None = None

    summary: str
    highlights: list[str] = Field(default_factory=list)
    domain: str = "其他"  # 知识领域（一级分类，可拓展）
    tags: list[str] = Field(default_factory=list)  # 具体技术关键词（二级）
    maturity: Maturity = Maturity.UNKNOWN
    audience: list[Audience] = Field(default_factory=list)
    product_use_case: str | None = None
    trend: Trend = Trend.UNKNOWN
    has_readme: bool = False

    extraction: ExtractionInfo


class FeedbackIn(BaseModel):
    """用户对单条内容的反馈。"""

    item_id: str
    vote: Literal["useful", "useless"]
    comment: str | None = None


class ReportSection(BaseModel):
    """总结报告的一个板块。"""

    key: str
    title: str
    paragraphs: list[str] = Field(default_factory=list)
    bullets: list[str] = Field(default_factory=list)


class Report(BaseModel):
    """第二层信息：点击 Card 后生成的结构化总结报告。"""

    model_config = ConfigDict(protected_namespaces=())

    item_id: str
    title: str
    generated_at: datetime
    model: str = "rule-based"
    status: ExtractionStatus = ExtractionStatus.FALLBACK
    source_hash: str = ""
    sections: list[ReportSection] = Field(default_factory=list)