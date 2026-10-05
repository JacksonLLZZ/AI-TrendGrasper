"""GitHub 抓取模块（可选）。

V1 默认使用内置示例数据，本模块在配置环境变量后启用：

    GITHUB_TOKEN=<your token>          # 必需，否则触发严格限流
    TREND_QUERIES=RAG,agent,llm        # 可选，默认见 DEFAULT_QUERIES

只依赖 GitHub 公开 REST API，无需额外 SDK。
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

GITHUB_SEARCH_API = "https://api.github.com/search/repositories"

DEFAULT_QUERIES = [
    "llm",
    "agent",
    "rag",
    "multimodal",
    "diffusion",
    "inference optimization",
]


def is_enabled() -> bool:
    """是否具备抓取条件（存在 GITHUB_TOKEN）。"""
    return bool(os.getenv("GITHUB_TOKEN"))


def _queries() -> list[str]:
    raw = os.getenv("TREND_QUERIES", "")
    if not raw.strip():
        return DEFAULT_QUERIES
    return [q.strip() for q in raw.split(",") if q.strip()]


def _headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _to_meta(repo: dict) -> dict:
    """把 GitHub 仓库对象裁剪成流水线需要的最小元数据。"""
    full_name = repo.get("full_name", "")
    return {
        "id": f"github:{full_name}",
        "source": "github",
        "full_name": full_name,
        "name": repo.get("name", ""),
        "url": repo.get("html_url", ""),
        "homepage": repo.get("homepage") or None,
        "description": repo.get("description"),
        "language": repo.get("language"),
        "license": (repo.get("license") or {}).get("spdx_id"),
        "topics": repo.get("topics", []) or [],
        "stars": repo.get("stargazers_count", 0),
        "forks": repo.get("forks_count", 0),
        "stars_delta_7d": 0,
        "last_updated": repo.get("pushed_at"),
        "has_readme": True,
    }


def fetch_repos(limit_per_query: int = 15) -> list[dict]:
    """按关键词抓取仓库元数据，按 full_name 去重。"""
    if not is_enabled():
        logger.info("未配置 GITHUB_TOKEN，跳过真实抓取。")
        return []

    seen: dict[str, dict] = {}
    headers = _headers()
    with httpx.Client(timeout=20.0, headers=headers) as client:
        for query in _queries():
            params = {
                "q": f"{query} stars:>200 pushed:>{_recent_date()}",
                "sort": "stars",
                "order": "desc",
                "per_page": limit_per_query,
            }
            try:
                resp = client.get(GITHUB_SEARCH_API, params=params)
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                logger.warning("抓取关键词 %s 失败：%s", query, exc)
                continue
            for repo in resp.json().get("items", []):
                meta = _to_meta(repo)
                if meta["full_name"]:
                    seen[meta["full_name"]] = meta
    return list(seen.values())


def _recent_date(days: int = 30) -> str:
    ts = datetime.now(timezone.utc).timestamp() - days * 86400
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def fetch_readme(full_name: str, max_chars: int = 4000) -> str:
    """抓取 README 文本（截断）。失败返回空字符串，交由降级逻辑处理。"""
    if not is_enabled() or not full_name:
        return ""
    url = f"https://api.github.com/repos/{full_name}/readme"
    headers = _headers()
    headers["Accept"] = "application/vnd.github.raw"
    try:
        with httpx.Client(timeout=20.0, headers=headers) as client:
            resp = client.get(url)
            resp.raise_for_status()
            return resp.text[:max_chars]
    except httpx.HTTPError as exc:
        logger.warning("抓取 %s 的 README 失败：%s", full_name, exc)
        return ""