"""Content-based recommender on movie metadata (实验一 内容推荐 · 实验三改进版).

Scoring (v2，解决"只用类型 one-hot → 22% 用户 Top-10 全同分"的问题):

     score = 0.55 · idf_cosine(profile, genres)
           + 0.15 · cosine(decade_profile, decade)
           + 0.30 · exposure_prior(movie)

1. 用户画像：对每部看过的电影按 (rating − 该用户平均分) 加权，
   再乘类型 IDF（出现越少类型区分度越高），得到 IDF 加权的类型向量；
   年代同理得到十年段偏好向量。
2. 曝光度先验：log(1+ 该电影被评次数) / log(1+ 最大次数)，
   把"只看类型"的候选往真实消费分布上拉一格。
3. 三者加权后取 Top-N，同分按 movie_id 升序。

内容推荐仍然只使用电影元数据 + 该用户自己的评分，
不使用其他用户的行为——这是它与 ItemCF 的区别。
"""
import math
from datetime import datetime, timezone
from pathlib import Path

from ..business import read_history
from ..contracts import Algorithm, RecommendationItem, RecommendationRequest, RecommendationResult
from .common import (catalog, empty_result, genre_idf, genre_index, idf_cosine, movie_decade,
                     movie_genres, read_artifact_gz, top_genres, top_scored)

MODEL_VERSION = "content-feature-v2"
# 打分权重：类型相似为主，年代与曝光度作为补充先验
W_GENRE, W_DECADE, W_EXPOSURE = 0.55, 0.15, 0.30


class ContentFeatureRecommender:
    """Content-based recommender with IDF genres, release decade and exposure prior."""

    model_manifest_path: str | Path

    def __init__(self, manifest_path: str | Path):
        self.model_manifest_path = Path(manifest_path)
        self._counts: dict[str, int] | None = None
        self._max_count = 1

    def _features(self) -> dict[str, int]:
        if self._counts is None:
            path = self.model_manifest_path.parent / "content_features.json.gz"
            if path.is_file():
                payload = read_artifact_gz(path)
                self._counts = payload.get("item_counts", {})
                self._max_count = max(1, int(payload.get("max_count", 1)))
            else:
                self._counts, self._max_count = {}, 1
        return self._counts

    def _exposure(self, movie_id: str) -> float:
        count = self._features().get(movie_id, 0)
        if count <= 0:
            return 0.0
        return math.log1p(count) / math.log1p(self._max_count)

    def recommend(self, request: RecommendationRequest) -> RecommendationResult:
        history = read_history(request)
        rated = {feedback.movie_id for feedback in history.ratings}
        ratings = {feedback.movie_id: float(feedback.rating) for feedback in history.ratings}
        if not ratings:
            # No ratings means no content profile at all: return an empty list
            # instead of pretending a global chart is content-based.
            return empty_result(request, Algorithm.content, MODEL_VERSION)

        user_mean = sum(ratings.values()) / len(ratings)
        weights = {movie_id: rating - user_mean for movie_id, rating in ratings.items()}

        idf = genre_idf()
        profile: dict[str, float] = {}
        decade_profile: dict[str, float] = {}
        for movie_id, weight in weights.items():
            if weight == 0.0:
                continue
            for genre in movie_genres(movie_id):
                profile[genre] = profile.get(genre, 0.0) + weight * idf.get(genre, 1.0)
            decade = movie_decade(movie_id)
            if decade != "unknown":
                decade_profile[decade] = decade_profile.get(decade, 0.0) + weight
        profile_norm = math.sqrt(sum(value * value for value in profile.values()))
        decade_norm = math.sqrt(sum(value * value for value in decade_profile.values()))
        index = genre_index()
        ordered = [0.0] * len(index)
        for genre, weight in profile.items():
            position = index.get(genre)
            if position is not None:
                ordered[position] = weight
        preferred = top_genres(ordered) if profile else []

        scores: dict[str, float] = {}
        for movie_id in catalog():
            if movie_id in rated:
                continue
            genre_part = idf_cosine(profile, profile_norm, movie_id) if profile_norm > 0 else 0.0
            decade = movie_decade(movie_id)
            decade_part = 0.0
            if decade_norm > 0 and decade != "unknown":
                decade_part = decade_profile.get(decade, 0.0) / decade_norm
            exposure = self._exposure(movie_id)
            scores[movie_id] = W_GENRE * genre_part + W_DECADE * max(decade_part, 0.0) + W_EXPOSURE * exposure

        items: list[RecommendationItem] = []
        for movie_id, score in top_scored(scores, request.limit):
            matched = [genre for genre in preferred if genre in movie_genres(movie_id)][:3]
            items.append(RecommendationItem(
                movie_id=movie_id,
                rank=len(items) + 1,
                algorithm=Algorithm.content,
                model_version=MODEL_VERSION,
                score=score,
                recall_sources=[f"偏好类型：{'、'.join(matched)}"] if matched else None,
            ))

        return RecommendationResult(
            algorithm=Algorithm.content,
            model_version=MODEL_VERSION,
            dataset_version=request.dataset_version,
            split_version=request.split_version,
            runtime_mode=request.runtime_mode,
            generated_at=datetime.now(timezone.utc),
            items=items,
        )
