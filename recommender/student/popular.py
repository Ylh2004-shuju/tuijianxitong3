"""Non-personalized popularity baseline (实验二 任务1).

The ranking is a Bayesian average of the sealed business snapshot, so a movie
with three 5.0 ratings cannot outrank a movie with thousands of strong ratings.
Seen movies are filtered out for usefulness, but the ordering itself never
depends on the current user: the result must never be presented as
personalized.
"""
from datetime import datetime, timezone
from pathlib import Path

from ..business import read_history
from ..contracts import Algorithm, RecommendationItem, RecommendationRequest, RecommendationResult
from .common import catalog, read_artifact

MODEL_VERSION = "popular-bayes-v1"


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

        items: list[RecommendationItem] = []
        for entry in entries:
            movie_id = entry["movie_id"]
            if movie_id not in approved or movie_id in seen:
                continue
            items.append(RecommendationItem(
                movie_id=movie_id,
                rank=len(items) + 1,
                algorithm=Algorithm.popular,
                model_version=MODEL_VERSION,
                score=entry["popularity_score"],
                recall_sources=[
                    f"全站 {entry['rating_count']} 人评分 · 平均 {entry['mean_rating']:.2f} 分"
                ],
            ))
            if len(items) == request.limit:
                break

        return RecommendationResult(
            algorithm=Algorithm.popular,
            model_version=MODEL_VERSION,
            dataset_version=request.dataset_version,
            split_version=request.split_version,
            runtime_mode=request.runtime_mode,
            generated_at=datetime.now(timezone.utc),
            items=items,
        )
