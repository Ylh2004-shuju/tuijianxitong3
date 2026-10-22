"""Non-personalized popularity baseline (实验二 任务1，v3 改版).

The ranking is the **positive-rating count** (how many people rated the movie
≥ 4.0) of the sealed business snapshot, followed by an MMR genre-diversity
re-rank. The count — not the average rating — is what predicts how many people
will rate the movie highly in the future. Seen movies are filtered out for
usefulness, but the ordering itself never depends on the current user: the
result must never be presented as personalized.
"""
from datetime import datetime, timezone
from pathlib import Path

from ..business import read_history
from ..contracts import Algorithm, RecommendationItem, RecommendationRequest, RecommendationResult
from .common import catalog, mmr_select, movie_genres, read_artifact

MODEL_VERSION = "popular-positive-mmr-v3"
# MMR 重排：候选池取高分榜前 500 部，λ=0.8 偏向相关性、0.2 惩罚类型重复
MMR_LAMBDA, MMR_POOL = 0.8, 500


class PopularRecommender:
    model_manifest_path: str | Path

    def __init__(self, manifest_path: str | Path):
        self.model_manifest_path = Path(manifest_path)
        self._entries: list[dict] | None = None

    def _load(self) -> list[dict]:
        if self._entries is None:
            artifact = read_artifact(self.model_manifest_path.parent / "popular_top.json")
            self._entries = artifact["items"]
        return self._entries

    def recommend(self, request: RecommendationRequest) -> RecommendationResult:
        entries = self._load()
        approved = catalog()
        seen = {feedback.movie_id for feedback in read_history(request).ratings}

        pool = [(entry["movie_id"], entry["popularity_score"]) for entry in entries
                if entry["movie_id"] in approved and entry["movie_id"] not in seen]
        # 榜单顺序固定（分数降序、同分 movie_id 升序），MMR 在此之上做类型去重
        pool.sort(key=lambda pair: (-pair[1], int(pair[0])))
        metadata = {entry["movie_id"]: entry for entry in entries}
        # MMR 决定"选哪 10 部"，展示顺序仍按贝叶斯分降序（同分按 movie_id 升序），
        # 这样榜单分数保持可比，多样性体现在集合本身。
        chosen = sorted(mmr_select(pool, request.limit, movie_genres, MMR_LAMBDA, MMR_POOL),
                        key=lambda pair: (-pair[1], int(pair[0])))
        items: list[RecommendationItem] = []
        for position, (movie_id, score) in enumerate(chosen, start=1):
            entry = metadata[movie_id]
            items.append(RecommendationItem(
                movie_id=movie_id,
                rank=position,
                algorithm=Algorithm.popular,
                model_version=MODEL_VERSION,
                score=score,
                recall_sources=[
                    f"全站 {entry['positive_count']} 人给了 4 分以上 · 平均 {entry['mean_rating']:.2f} 分"
                ],
            ))

        return RecommendationResult(
            algorithm=Algorithm.popular,
            model_version=MODEL_VERSION,
            dataset_version=request.dataset_version,
            split_version=request.split_version,
            runtime_mode=request.runtime_mode,
            generated_at=datetime.now(timezone.utc),
            items=items,
        )
