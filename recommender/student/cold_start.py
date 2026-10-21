"""Cold-start entry for brand-new accounts (实验二 任务2).

Three explicit states, each one labelled in the response so the interface can
never sell a global chart as personalization:

  * no rating >= 3.5 and no favorite  -> non-personalized popular list;
  * at least one rating >= 3.5 or a favorite -> genre profile from those
    signals, slightly re-weighted by popularity (85% / 15%);
  * a profile that matches no catalog genre -> explicit popular fallback.

Only favourites and ratings are read; no extra table, no invented profile.
"""
from datetime import datetime, timezone
from pathlib import Path

from ..business import read_history
from ..contracts import Algorithm, RecommendationItem, RecommendationRequest, RecommendationResult
from .common import (catalog, empty_result, genre_cosine, genre_profile, profile_norm, read_artifact,
                     top_genres, top_scored)

MODEL_VERSION = "coldstart-v1"
RATING_MIN = 3.5
FAVORITE_WEIGHT = 1.0
CONTENT_WEIGHT = 0.85
POPULARITY_WEIGHT = 0.15


class ColdStartRecommender:
    model_manifest_path: str | Path

    def __init__(self, manifest_path: str | Path):
        self.model_manifest_path = Path(manifest_path)
        self._popular: list[dict] | None = None
        self._popular_norm: dict[str, float] | None = None

    def _load_popular(self) -> list[dict]:
        if self._popular is None:
            artifact = read_artifact(self.model_manifest_path.parent / "popular_top.json")
            self._popular = artifact["items"]
            values = [entry["popularity_score"] for entry in self._popular]
            low, high = min(values), max(values)
            span = high - low or 1.0
            self._popular_norm = {entry["movie_id"]: (entry["popularity_score"] - low) / span
                                  for entry in self._popular}
        return self._popular

    def _popular_items(self, request, seen: set[str], reason: str) -> list[RecommendationItem]:
        items: list[RecommendationItem] = []
        for entry in self._load_popular():
            movie_id = entry["movie_id"]
            if movie_id in seen or movie_id not in catalog():
                continue
            items.append(RecommendationItem(
                movie_id=movie_id,
                rank=len(items) + 1,
                algorithm=Algorithm.cold_start,
                model_version=MODEL_VERSION,
                score=entry["popularity_score"],
                recall_sources=[reason.format(count=entry["rating_count"], mean=entry["mean_rating"])],
            ))
            if len(items) == request.limit:
                break
        return items

    def recommend(self, request: RecommendationRequest) -> RecommendationResult:
        history = read_history(request)
        rated = {feedback.movie_id for feedback in history.ratings}
        favourites = list(history.favorite_movie_ids)

        weights: dict[str, float] = {}
        rating_seeds = 0
        for feedback in history.ratings:
            rating = float(feedback.rating)
            if rating >= RATING_MIN:
                weights[feedback.movie_id] = rating - 3.0
                rating_seeds += 1
        for movie_id in favourites:
            # A favourite is a choice, not a rating: weaker than a 5-star vote.
            weights[movie_id] = weights.get(movie_id, 0.0) + FAVORITE_WEIGHT

        if not weights:
            items = self._popular_items(request, rated, "热门榜（非个性化）：全站 {count} 人评分，平均 {mean:.2f} 分")
            return RecommendationResult(
                algorithm=Algorithm.cold_start, model_version=MODEL_VERSION,
                dataset_version=request.dataset_version, split_version=request.split_version,
                runtime_mode=request.runtime_mode, generated_at=datetime.now(timezone.utc), items=items,
            )

        profile = genre_profile(weights)
        norm = profile_norm(profile)
        if norm == 0.0:
            items = self._popular_items(request, rated | set(favourites),
                                        "热门兜底（非个性化）：全站 {count} 人评分，平均 {mean:.2f} 分")
            return RecommendationResult(
                algorithm=Algorithm.cold_start, model_version=MODEL_VERSION,
                dataset_version=request.dataset_version, split_version=request.split_version,
                runtime_mode=request.runtime_mode, generated_at=datetime.now(timezone.utc), items=items,
            )

        self._load_popular()
        popularity = self._popular_norm or {}
        excluded = rated | set(favourites)
        scores: dict[str, float] = {}
        for movie_id in catalog():
            if movie_id in excluded:
                continue
            affinity = genre_cosine(profile, norm, movie_id)
            if affinity <= 0.0:
                continue
            scores[movie_id] = CONTENT_WEIGHT * affinity + POPULARITY_WEIGHT * popularity.get(movie_id, 0.0)

        types = "、".join(top_genres(profile)) or "多类型"
        reason = f"偏好类型：{types}（{rating_seeds} 条 ≥{RATING_MIN:g} 分评分 + {len(favourites)} 个收藏）"
        items = [
            RecommendationItem(
                movie_id=movie_id, rank=rank, algorithm=Algorithm.cold_start,
                model_version=MODEL_VERSION, score=score, recall_sources=[reason],
            )
            for rank, (movie_id, score) in enumerate(top_scored(scores, request.limit), 1)
        ]
        if not items:
            items = self._popular_items(request, excluded,
                                        "热门兜底（非个性化）：全站 {count} 人评分，平均 {mean:.2f} 分")

        return RecommendationResult(
            algorithm=Algorithm.cold_start, model_version=MODEL_VERSION,
            dataset_version=request.dataset_version, split_version=request.split_version,
            runtime_mode=request.runtime_mode, generated_at=datetime.now(timezone.utc), items=items,
        )
