"""Content-based recommender using movie genre features (实验一 内容推荐).

Algorithm:
  1. Load one-hot genre vectors for the frozen 8,459-movie catalog (cached once).
  2. Build the user profile as the rating-weighted sum of rated genre vectors
     (weight = rating - 3.0: likes push a genre up, dislikes pull it down).
  3. Cosine similarity between the profile and every unseen catalog movie.
  4. Remove rated movies; sort by the rounded score, tie-break by movie_id asc.
  5. Explain each result with the preferred genres it matches.

Content only relies on movie metadata plus the user's own ratings.  It knows
nothing about what other users watched: that is the difference against ItemCF.
"""
from datetime import datetime, timezone
from pathlib import Path

from ..business import read_history
from ..contracts import Algorithm, RecommendationItem, RecommendationRequest, RecommendationResult
from .common import (catalog, empty_result, genre_cosine, genre_profile, movie_genres, profile_norm,
                     top_genres, top_scored)

MODEL_VERSION = "content-genre-v1"


class ContentGenreRecommender:
    """Content-based recommender using movie genres as features."""

    model_manifest_path: str | Path

    def __init__(self, manifest_path: str | Path):
        self.model_manifest_path = Path(manifest_path)

    def recommend(self, request: RecommendationRequest) -> RecommendationResult:
        history = read_history(request)
        rated = {feedback.movie_id for feedback in history.ratings}
        if not rated:
            # No ratings means no genre profile at all: return an empty list
            # instead of pretending a global chart is content-based.
            return empty_result(request, Algorithm.content, MODEL_VERSION)

        profile = genre_profile({feedback.movie_id: float(feedback.rating) - 3.0 for feedback in history.ratings})
        norm = profile_norm(profile)
        if norm == 0.0:
            return empty_result(request, Algorithm.content, MODEL_VERSION)

        scores = {movie_id: genre_cosine(profile, norm, movie_id)
                  for movie_id in catalog() if movie_id not in rated}
        preferred = top_genres(profile)

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
