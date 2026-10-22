"""实验四 S1：多算法融合推荐（Reciprocal Rank Fusion）。

融合不训练新模型，只把四个成员算法的**排序结果**按 RRF 合并：

    score(d) = Σ_a  w_a / (k + rank_a(d))

其中 rank_a(d) 是电影 d 在算法 a 的候选列表里的名次（1 起），不在列表里则不贡献。
k 取 60（RRF 原始论文的经验值），它把"前几名"的差距压平，避免某一个算法
靠单点高分垄断结果。

为什么用名次而不是分数：四个算法的分数量纲完全不同（ItemCF 是相似度和、
热门是高分人数占比、内容是 0~1 的特征相似度、ALS 是 1~5 的预测评分），
直接加权求和没有意义；名次是无量纲的，才可比较。

每个推荐项都带 `recall_ranks`，写明它来自哪几个算法、各排第几名——
这是"结果由真实算法算出"而不是硬编码的直接证据。
"""
from datetime import datetime, timezone
from pathlib import Path

from ..contracts import (Algorithm, RecommendationRequest, RecommendationResult,
                         RecommendationItem)
from .common import empty_result, read_artifact, top_scored

MODEL_VERSION = "fusion-rrf-v1"
# 成员算法（固定四个，不含 cold_start 与 fusion 自身）
MEMBERS = (Algorithm.popular, Algorithm.content, Algorithm.itemcf, Algorithm.als)
# RRF 常数与每个算法取的候选深度。
# 深度取 10：融合的是四个算法各自呈现给用户的 Top-10，与页面所见一致；
# 取更深（如 50）精度提升有限，但热门榜的 MMR 重排是 O(深度 × 候选池)，
# 离线评价会从分钟级涨到二十分钟以上，不划算。
RRF_K = 60
DEPTH = 10


def rrf_scores(ranked: dict[str, list[str]], weights: dict[str, float] | None = None,
               k: int = RRF_K) -> dict[str, float]:
    """ranked: {算法名: [movie_id, ...]}（按名次排好）；返回 RRF 原始分。"""
    weights = weights or {}
    scores: dict[str, float] = {}
    for name, ids in ranked.items():
        w = float(weights.get(name, 1.0))
        for position, movie_id in enumerate(ids, start=1):
            scores[movie_id] = scores.get(movie_id, 0.0) + w / (k + position)
    return scores


def rrf_ranks(ranked: dict[str, list[str]]) -> dict[str, dict[str, int]]:
    """返回 {movie_id: {算法名: 名次}}，用于前端的来源分解与验收核对。"""
    out: dict[str, dict[str, int]] = {}
    for name, ids in ranked.items():
        for position, movie_id in enumerate(ids, start=1):
            out.setdefault(movie_id, {})[name] = position
    return out


class FusionRecommender:
    model_manifest_path: str | Path

    def __init__(self, manifest_path: str | Path):
        self.model_manifest_path = Path(manifest_path)
        self._config: dict | None = None

    def _load(self) -> dict:
        if self._config is None:
            self._config = read_artifact(self.model_manifest_path.parent / "fusion_config.json")
        return self._config

    def _member_lists(self, request: RecommendationRequest) -> dict[str, list[str]]:
        from ..contracts import PROVIDERS

        ranked: dict[str, list[str]] = {}
        for member in MEMBERS:
            provider = PROVIDERS.get(member)
            if provider is None:
                continue
            sub = RecommendationRequest(
                subject_kind=request.subject_kind,
                subject_id=request.subject_id,
                limit=DEPTH,
                runtime_mode=request.runtime_mode,
                dataset_version=request.dataset_version,
                split_version=request.split_version,
            )
            try:
                result = provider.recommend(sub)
            except Exception:  # 单个成员失败不应拖垮整体融合
                continue
            ids = [item.movie_id for item in result.items]
            if ids:
                ranked[member.value] = ids
        return ranked

    def recommend(self, request: RecommendationRequest) -> RecommendationResult:
        config = self._load()
        k = int(config.get("k", RRF_K))
        depth = int(config.get("depth", DEPTH))
        weights = config.get("weights", {})

        ranked = self._member_lists(request)
        if not ranked:
            return empty_result(request, Algorithm.fusion, MODEL_VERSION)

        scores = rrf_scores(ranked, weights, k)
        ranks = rrf_ranks(ranked)
        highest = max(scores.values()) or 1.0

        # 归一化到 (0,1] 便于展示；除以同一个正数不改变排序
        normalized = {movie_id: round(value / highest, 6) for movie_id, value in scores.items()}

        items: list[RecommendationItem] = []
        for movie_id, score in top_scored(normalized, request.limit):
            breakdown = ranks.get(movie_id, {})
            detail = "、".join(f"{name} 第{position}名"
                              for name, position in sorted(breakdown.items(), key=lambda p: p[1]))
            items.append(RecommendationItem(
                movie_id=movie_id,
                rank=len(items) + 1,
                algorithm=Algorithm.fusion,
                model_version=MODEL_VERSION,
                score=score,
                recall_sources=[f"RRF 融合 {len(breakdown)} 个算法：{detail}"],
                recall_ranks=dict(breakdown) or None,
            ))

        return RecommendationResult(
            algorithm=Algorithm.fusion,
            model_version=MODEL_VERSION,
            dataset_version=request.dataset_version,
            split_version=request.split_version,
            runtime_mode=request.runtime_mode,
            generated_at=datetime.now(timezone.utc),
            items=items,
        )
