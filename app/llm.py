"""LLM 结构化提取模块。

两类能力：
1. extract()        —— 把非结构化仓库信息提炼成看板 Card 的结构化字段（一级：领域 / 二级：技术关键词）。
2. generate_report() —— 点击 Card 后生成结构化「总结报告」（项目简介、原理讲解、亮点等板块）。

统一内置降级策略，未配置大模型凭证时自动走规则兜底，示例数据模式无需任何 Key。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone

import httpx

from .models import (
    Audience,
    ExtractionInfo,
    ExtractionStatus,
    Maturity,
    SUGGESTED_DOMAINS,
    Trend,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 受控词表
# ---------------------------------------------------------------------------

TAG_VOCAB = [
    "LLM", "Agent", "RAG", "多模态", "微调", "RLHF", "提示工程", "Embedding",
    "向量数据库", "推理加速", "量化", "蒸馏", "分布式训练", "模型评估", "语音",
    "图像生成", "视频生成", "代码生成", "OCR", "重排序", "安全护栏", "数据标注",
    "MLOps", "知识图谱", "工作流编排", "工具调用", "长上下文", "记忆管理", "MoE",
    "扩散模型", "端到端", "感知", "运动规划", "世界模型", "强化学习", "具身智能",
    "VLA", "知识库",
]
TAG_FALLBACK = "其他"
DOMAIN_FALLBACK = "其他"

SYSTEM_PROMPT = """你是「AI 趋势雷达」的数据结构化引擎。你的唯一任务：读取输入的 GitHub 仓库信息，输出一个严格符合下方 Schema 的 JSON 对象。

【输出硬性规则】
1. 只输出一个 JSON 对象。禁止任何解释、前言、结语，禁止 Markdown 代码块标记（不要输出 ```json 或 ```）。
2. 字段名与类型必须与 Schema 完全一致，不得新增、删除、重命名字段。
3. 所有内容必须基于输入事实，严禁编造功能、性能或数据。信息不足时按【缺省规则】处理，不得猜测。
4. summary、highlights、product_use_case 使用简体中文；domain、tags、maturity、audience、trend 使用规定枚举值。
5. domain 只能选 1 个（一级分类：知识领域）；tags 只能选 1-2 个（二级分类：具体技术关键词），且必须来自【标签词表】。
6. 不得输出任何 JSON 之外的字符。

【一级分类 domain（知识领域，单选）】
请优先从以下建议领域中选择；若确实不属于其中任何一项，可给出一个更贴切的新领域名称（保持 2-6 字）：
大模型基础 | Agent 与自动化 | 检索与知识 | 多模态生成 | 语音与音频 | 推理与部署 | 训练与微调 | 数据与向量 | 自动驾驶 | 机器人 / 具身智能 | 医疗健康 | 金融科技 | 教育科研 | 企业应用 | 开发者工具 | 其他

【二级分类 tags（具体技术关键词，最多选 2 个，必须是以下之一）】
LLM, Agent, RAG, 多模态, 微调, RLHF, 提示工程, Embedding, 向量数据库, 推理加速, 量化, 蒸馏, 分布式训练, 模型评估, 语音, 图像生成, 视频生成, 代码生成, OCR, 重排序, 安全护栏, 数据标注, MLOps, 知识图谱, 工作流编排, 工具调用, 长上下文, 记忆管理, MoE, 扩散模型, 端到端, 感知, 运动规划, 世界模型, 强化学习, 具身智能, VLA, 知识库

【字段生成要求】
- summary：一句话（不超过 40 字），说明「它是什么 + 核心亮点」，突出差异化，不要复述仓库名。
- highlights：1-3 条，每条不超过 30 字，技术价值与产品价值兼顾，不要空话套话。
- maturity：production=有稳定版本/生产案例；usable=可用但仍在快速迭代；experimental=早期实验；无法判断填 unknown。
- audience：判断更适合 technical（研发）还是 product（产品），可两者都选，无法判断给空数组。
- product_use_case：用产品语言描述适用场景（不超过 40 字）；纯底层库若无可落地场景填 null。
- trend：仅根据输入中的 star 增量与更新时间判断；信息不足填 unknown。
- confidence：0-1，表示你对本次提取整体准确度的把握。

【缺省规则】
- 若 has_readme=false 或 README 摘录为空：summary 只能基于 name、description、topics、language 保守推断，confidence 不高于 0.4，maturity 与 trend 填 unknown，tags 尽量由 topics 映射。
- 若连 description 也为空：summary 填「信息不足，暂无法生成亮点」，highlights 填 ["暂无描述，信息有限"]，domain 填「其他」。
- 任何不确定的判断都要降低 confidence，而不是编造。

【输出 Schema（只包含以下字段）】
{
  "summary": "string",
  "highlights": ["string"],
  "domain": "string",
  "tags": ["string"],
  "maturity": "string",
  "audience": ["string"],
  "product_use_case": "string|null",
  "trend": "string",
  "confidence": 0.0
}

现在开始。只输出 JSON。"""

USER_PROMPT_TEMPLATE = """请结构化以下 GitHub 仓库，严格按 System 中定义的 Schema 输出 JSON。

## 仓库元数据（事实来源）
- full_name: {full_name}
- url: {url}
- description: {description}
- primary_language: {language}
- topics: {topics}
- stars: {stars}
- stars_delta_7d: {stars_delta_7d}
- license: {license}
- last_updated: {last_updated}
- has_readme: {has_readme}

## README 摘录（最多 4000 字符，超出部分已截断）
{readme}

## 说明
- 若 has_readme=false，上述 README 摘录为空，请严格执行【缺省规则】。
- 只输出 JSON 对象，不要输出任何其他内容。"""


# ---------------------------------------------------------------------------
# 报告 Prompt（第二层：点击 Card 后的总结报告）
# ---------------------------------------------------------------------------

REPORT_SYSTEM_PROMPT = """你是「AI 趋势雷达」的技术分析师。你的任务：阅读给定的 GitHub 仓库信息与 README，输出一份面向公司内部研发与产品团队的结构化总结报告。

【输出硬性规则】
1. 只输出一个 JSON 对象。禁止任何解释、前言、结语，禁止 Markdown 代码块标记。
2. 字段名与类型必须与下方 Schema 完全一致。
3. 内容必须基于给定材料，严禁编造不存在的功能、指标或数据。材料不足时如实说明「材料中未提及」。
4. 全部使用简体中文。
5. 段落类字段用 1-3 句话讲清楚；亮点用短句条目。不要空话套话。

【报告结构要求】
- intro：项目简介。它是什么、由谁维护/面向谁、解决什么问题。
- principle：原理讲解。用通俗但准确的语言说明核心技术原理与关键机制（如架构、算法、关键设计）。若材料不足以讲清原理，说明「材料中未详细说明」并给出可推断的技术方向。
- highlights：核心亮点，2-4 条短句。
- scenarios：适用场景。研发可用在哪、产品可借鉴什么。
- limitations：局限与风险。如成熟度、License、部署成本、已知限制；材料不足则说明信息有限。

【输出 Schema】
{
  "intro": "string",
  "principle": "string",
  "highlights": ["string"],
  "scenarios": "string",
  "limitations": "string",
  "confidence": 0.0
}

现在开始。只输出 JSON。"""

REPORT_USER_TEMPLATE = """请为以下 GitHub 仓库生成结构化总结报告。

## 仓库信息
- full_name: {full_name}
- url: {url}
- description: {description}
- primary_language: {language}
- topics: {topics}
- stars: {stars}
- license: {license}
- domain: {domain}
- tags: {tags}
- 已有摘要: {summary}

## README 全文（最多 6000 字符，已截断）
{readme}

## 说明
- 若 README 为空，请基于上述信息尽力分析，并在相应板块说明信息有限。
- 只输出 JSON 对象，不要输出任何其他内容。"""


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

def _llm_config() -> dict | None:
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        return None
    return {
        "api_key": api_key,
        "base_url": os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
        "model": os.getenv("LLM_MODEL", "gpt-4o-mini"),
    }


def is_enabled() -> bool:
    return _llm_config() is not None


def content_hash(*parts: str) -> str:
    """用于报告缓存的来源指纹。"""
    digest = hashlib.sha256()
    for part in parts:
        digest.update((part or "").encode("utf-8"))
    return digest.hexdigest()[:16]


# ---------------------------------------------------------------------------
# JSON 解析（四级降级的前三级）
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```(?:json)?", re.IGNORECASE)
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")


def _strip_fences(text: str) -> str:
    cleaned = _FENCE_RE.sub("", text).replace("`", "").strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        return cleaned[start : end + 1]
    return cleaned


def _repair(text: str) -> str:
    """修复常见 JSON 语法问题：尾逗号、中文标点。"""
    fixed = _TRAILING_COMMA_RE.sub(r"\1", text)
    return fixed.replace("，", ",").replace("：", ":")


def parse_model_output(text: str) -> dict | None:
    """按 L0 → L1 → L2 逐级尝试解析，全部失败返回 None。"""
    for candidate in (text, _strip_fences(text), _repair(_strip_fences(text))):
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            continue
    return None


# ---------------------------------------------------------------------------
# 归一化与校验
# ---------------------------------------------------------------------------

def _as_str_list(value, limit: int) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()][:limit]


def normalize(data: dict) -> tuple[dict, bool]:
    """把模型输出归一化到受控枚举，返回 (字段, 是否需要修补)。"""
    repaired = False

    summary = str(data.get("summary") or "").strip()[:120]
    if not summary:
        summary = "暂无简介"
        repaired = True

    highlights = _as_str_list(data.get("highlights"), 3)
    if not highlights:
        highlights = ["暂无亮点摘要"]
        repaired = True

    domain = str(data.get("domain") or "").strip()[:12]
    if not domain:
        domain = DOMAIN_FALLBACK
        repaired = True

    tags = [t for t in _as_str_list(data.get("tags"), 5) if t in TAG_VOCAB]
    tags = list(dict.fromkeys(tags))[:2] or [TAG_FALLBACK]
    if tags == [TAG_FALLBACK]:
        repaired = True

    maturity = str(data.get("maturity") or "").strip()
    if maturity not in [m.value for m in Maturity]:
        maturity = Maturity.UNKNOWN.value
        repaired = True

    trend = str(data.get("trend") or "").strip()
    if trend not in [t.value for t in Trend]:
        trend = Trend.UNKNOWN.value
        repaired = True

    audience = [a for a in _as_str_list(data.get("audience"), 2) if a in [x.value for x in Audience]]

    raw_use_case = data.get("product_use_case")
    product_use_case = str(raw_use_case).strip()[:120] if raw_use_case else None

    try:
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0.5))))
    except (TypeError, ValueError):
        confidence = 0.5
        repaired = True

    return {
        "summary": summary,
        "highlights": highlights,
        "domain": domain,
        "tags": tags,
        "maturity": maturity,
        "audience": audience,
        "product_use_case": product_use_case,
        "trend": trend,
        "confidence": confidence,
    }, repaired


# ---------------------------------------------------------------------------
# 规则兜底（第四级）
# ---------------------------------------------------------------------------

_DOMAIN_KEYWORDS = [
    ("自动驾驶", ("autonomous driving", "self-driving", "adas", "bev", "自动驾驶", "车载")),
    ("机器人 / 具身智能", ("robot", "embodied", "manipulation", "vla", "机器人", "具身")),
    ("医疗健康", ("medical", "healthcare", "biomedical", "医疗", "健康")),
    ("金融科技", ("fintech", "trading", "finance", "金融")),
    ("教育科研", ("education", "learning platform", "教育", "科研")),
    ("语音与音频", ("speech", "audio", "tts", "asr", "voice", "语音", "音频")),
    ("多模态生成", ("diffusion", "image generation", "video", "multimodal", "图像", "视频", "多模态")),
    ("检索与知识", ("rag", "retrieval", "knowledge base", "检索", "知识库")),
    ("Agent 与自动化", ("agent", "autogen", "copilot", "workflow", "智能体", "编排")),
    ("推理与部署", ("inference", "serving", "quantization", "vllm", "推理", "量化", "部署")),
    ("训练与微调", ("training", "fine-tun", "finetune", "lora", "rlhf", "训练", "微调")),
    ("数据与向量", ("vector", "database", "embedding", "storage", "向量", "数据")),
    ("企业应用", ("chat ui", "assistant", "self-hosted", "企业", "助手")),
    ("开发者工具", ("sdk", "cli", "framework", "library", "工具", "框架")),
    ("大模型基础", ("llm", "foundation model", "transformer", "大模型", "基座")),
]

_TOPIC_TAG_MAP = {
    "llm": "LLM", "large-language-model": "LLM", "agent": "Agent", "agents": "Agent",
    "rag": "RAG", "retrieval": "RAG", "multimodal": "多模态", "diffusion": "扩散模型",
    "stable-diffusion": "图像生成", "text-to-image": "图像生成", "text-to-video": "视频生成",
    "tts": "语音", "speech": "语音", "asr": "语音", "whisper": "语音", "ocr": "OCR",
    "fine-tuning": "微调", "lora": "微调", "rlhf": "RLHF", "prompt-engineering": "提示工程",
    "embedding": "Embedding", "embeddings": "Embedding", "vector-database": "向量数据库",
    "vllm": "推理加速", "quantization": "量化", "distributed-training": "分布式训练",
    "evaluation": "模型评估", "benchmark": "模型评估", "code-generation": "代码生成",
    "autonomous-driving": "端到端", "robotics": "具身智能", "world-model": "世界模型",
    "reinforcement-learning": "强化学习", "moe": "MoE",
}


def rule_based_extract(meta: dict) -> dict:
    """不依赖大模型，纯规则拼装，保证卡片可渲染。"""
    text = " ".join(str(meta.get(k) or "") for k in ("name", "description", "language")).lower()
    text += " " + " ".join(meta.get("topics") or []).lower()

    domain = DOMAIN_FALLBACK
    for name, keywords in _DOMAIN_KEYWORDS:
        if any(kw in text for kw in keywords):
            domain = name
            break

    tags: list[str] = []
    for topic in meta.get("topics") or []:
        mapped = _TOPIC_TAG_MAP.get(str(topic).lower())
        if mapped and mapped not in tags:
            tags.append(mapped)
    tags = tags[:2] or [TAG_FALLBACK]

    description = str(meta.get("description") or "").strip()
    summary = description[:40] if description else "暂无简介"

    return {
        "summary": summary,
        "highlights": ["暂无 AI 摘要，仅展示原始信息"],
        "domain": domain,
        "tags": tags,
        "maturity": Maturity.UNKNOWN.value,
        "audience": [],
        "product_use_case": None,
        "trend": Trend.UNKNOWN.value,
        "confidence": 0.0,
    }


# ---------------------------------------------------------------------------
# 调用封装
# ---------------------------------------------------------------------------

def _chat(config: dict, system_prompt: str, user_prompt: str) -> str | None:
    payload = {
        "model": config["model"],
        "temperature": 0.1,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    headers = {"Authorization": f"Bearer {config['api_key']}", "Content-Type": "application/json"}
    try:
        resp = httpx.post(
            f"{config['base_url']}/chat/completions",
            json=payload,
            headers=headers,
            timeout=90.0,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except (httpx.HTTPError, KeyError, IndexError) as exc:
        logger.warning("调用大模型失败：%s", exc)
        return None


# ---------------------------------------------------------------------------
# 对外入口：结构化提取
# ---------------------------------------------------------------------------

def extract(meta: dict, readme: str = "") -> tuple[dict, ExtractionInfo]:
    """提取看板 Card 的 AI 字段，返回 (字段字典, 提取元信息)。始终返回可用结果。"""
    now = datetime.now(timezone.utc)
    config = _llm_config()

    if not config:
        return rule_based_extract(meta), ExtractionInfo(
            status=ExtractionStatus.FALLBACK,
            model="rule-based",
            generated_at=now,
            confidence=0.0,
            retries=0,
        )

    user_prompt = USER_PROMPT_TEMPLATE.format(
        full_name=meta.get("full_name"),
        url=meta.get("url"),
        description=meta.get("description"),
        language=meta.get("language"),
        topics=", ".join(meta.get("topics") or []),
        stars=meta.get("stars"),
        stars_delta_7d=meta.get("stars_delta_7d"),
        license=meta.get("license"),
        last_updated=meta.get("last_updated"),
        has_readme=meta.get("has_readme", False),
        readme=readme or "（无）",
    )

    for attempt in range(2):  # L3：最多重试 1 次
        content = _chat(config, SYSTEM_PROMPT, user_prompt)
        if content is None:
            continue
        parsed = parse_model_output(content)
        if parsed is None:
            logger.warning("大模型输出非法 JSON，第 %d 次", attempt + 1)
            continue
        fields, repaired = normalize(parsed)
        status = ExtractionStatus.PARTIAL if repaired else ExtractionStatus.SUCCESS
        return fields, ExtractionInfo(
            status=status,
            model=config["model"],
            generated_at=now,
            confidence=fields["confidence"],
            retries=attempt,
        )

    logger.warning("大模型提取失败，降级为规则兜底：%s", meta.get("full_name"))
    return rule_based_extract(meta), ExtractionInfo(
        status=ExtractionStatus.FALLBACK,
        model="rule-based",
        generated_at=now,
        confidence=0.0,
        retries=1,
    )


# ---------------------------------------------------------------------------
# 对外入口：总结报告
# ---------------------------------------------------------------------------

_REPORT_SECTIONS = [
    ("intro", "项目简介"),
    ("principle", "原理讲解"),
    ("highlights", "核心亮点"),
    ("scenarios", "适用场景"),
    ("limitations", "局限与风险"),
]


def _clean_paragraph(value) -> str:
    if isinstance(value, list):
        return "；".join(str(v).strip() for v in value if str(v).strip())
    return str(value or "").strip()


def _fallback_report_sections(item: dict) -> list[dict]:
    """无大模型时，用已有字段拼装一份可读的报告，保证页面不空白。"""
    description = item.get("description") or ""
    summary = item.get("summary") or "暂无简介"
    tags = "、".join(item.get("tags") or []) or "暂无"

    intro_parts = [p for p in (summary, description) if p]
    highlights = item.get("highlights") or ["暂无亮点信息"]
    scenarios = item.get("product_use_case") or "材料不足，暂无法给出适用场景，建议查看仓库 README 进一步判断。"
    limitations = [
        f"技术关键词：{tags}",
        "当前未配置大模型，本页为基于已有字段的规则拼装结果；配置 LLM_API_KEY 后可生成原理讲解与分析。",
    ]

    return [
        {"key": "intro", "title": "项目简介", "paragraphs": intro_parts or ["暂无简介"], "bullets": []},
        {"key": "principle", "title": "原理讲解", "paragraphs": ["暂未生成原理讲解（需配置大模型）。"], "bullets": []},
        {"key": "highlights", "title": "核心亮点", "paragraphs": [], "bullets": highlights},
        {"key": "scenarios", "title": "适用场景", "paragraphs": [scenarios], "bullets": []},
        {"key": "limitations", "title": "局限与风险", "paragraphs": [], "bullets": limitations},
    ]


def generate_report(item: dict, readme: str = "") -> tuple[list[dict], ExtractionStatus, str]:
    """生成结构化总结报告，返回 (板块列表, 状态, 使用的模型)。"""
    config = _llm_config()
    if not config:
        return _fallback_report_sections(item), ExtractionStatus.FALLBACK, "rule-based"

    user_prompt = REPORT_USER_TEMPLATE.format(
        full_name=item.get("full_name"),
        url=item.get("url"),
        description=item.get("description"),
        language=item.get("language"),
        topics=", ".join(item.get("topics") or []),
        stars=item.get("stars"),
        license=item.get("license"),
        domain=item.get("domain"),
        tags=", ".join(item.get("tags") or []),
        summary=item.get("summary"),
        readme=readme or "（无）",
    )

    for attempt in range(2):
        content = _chat(config, REPORT_SYSTEM_PROMPT, user_prompt)
        parsed = parse_model_output(content) if content else None
        if parsed is None:
            logger.warning("报告生成输出非法，第 %d 次", attempt + 1)
            continue

        sections = []
        repaired = False
        for key, title in _REPORT_SECTIONS:
            raw = parsed.get(key)
            if key == "highlights":
                bullets = _as_str_list(raw, 4)
                if not bullets:
                    bullets, repaired = ["暂无亮点信息"], True
                sections.append({"key": key, "title": title, "paragraphs": [], "bullets": bullets})
            else:
                text = _clean_paragraph(raw)
                if not text:
                    text, repaired = "材料中未提及。", True
                sections.append({"key": key, "title": title, "paragraphs": [text], "bullets": []})

        status = ExtractionStatus.PARTIAL if repaired else ExtractionStatus.SUCCESS
        return sections, status, config["model"]

    return _fallback_report_sections(item), ExtractionStatus.FALLBACK, "rule-based"