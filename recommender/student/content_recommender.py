"""Content-based recommender using movie genre features.

Algorithm:
  1. Build a genre feature vector for each movie (one-hot encoding of genres)
  2. Build user profile as weighted average of rated movies' genre vectors
     (weights = normalized rating - mean rating, so high ratings add positive weight,
      low ratings add negative weight, but we only use positive feedback for recommendation)
  3. Compute cosine similarity between user profile and all candidate movies
  4. Exclude movies the user has already rated
  5. Return top-N recommendations sorted by score (descending), tie-break by movie_id ascending
"""
from datetime import datetime, timezone
from pathlib import Path

from ..contracts import Algorithm, RecommendationRequest, RecommendationItem, RecommendationResult
from ..data import FrozenData
from ..business import read_history


class ContentGenreRecommender:
    """Content-based recommender using movie genres as features."""

    model_manifest_path: str | Path

    def __init__(self, manifest_path: str | Path):
        self.model_manifest_path = Path(manifest_path)
        self._genre_index: dict[str, int] | None = None
        self._movie_genres: dict[str, list[str]] | None = None
        self._movie_vectors: dict[str, list[float]] | None = None

    def _load_features(self) -> None:
        """Load movie genre features from frozen data."""
        if self._movie_vectors is not None:
            return

        data = FrozenData()
        # Collect all unique genres and per-movie genre lists
        all_genres: set[str] = set()
        movie_genres: dict[str, list[str]] = {}

        for row in data.rows("movies.csv"):
            movie_id = row["movie_id"]
            genres_raw = row["genres"]
            if genres_raw == "(no genres listed)" or not genres_raw:
                genres = []
            else:
                genres = genres_raw.split("|")
            movie_genres[movie_id] = genres
            all_genres.update(genres)

        # Sort genres for deterministic index mapping
        sorted_genres = sorted(all_genres)
        genre_index = {g: i for i, g in enumerate(sorted_genres)}

        # Build one-hot vectors
        movie_vectors: dict[str, list[float]] = {}
        for movie_id, genres in movie_genres.items():
            vec = [0.0] * len(sorted_genres)
            for g in genres:
                vec[genre_index[g]] = 1.0
            movie_vectors[movie_id] = vec

        self._genre_index = genre_index
        self._movie_genres = movie_genres
        self._movie_vectors = movie_vectors

    def recommend(self, request: RecommendationRequest) -> RecommendationResult:
        self._load_features()

        # Get user history (ratings)
        history = read_history(request)
        rated_ids = {fb.movie_id for fb in history.ratings}

        # Compute user profile: weighted sum of genre vectors
        # Use rating - 3.0 as weight (positive = likes, negative = dislikes)
        user_vec = [0.0] * len(self._genre_index)
        total_pos_weight = 0.0

        for fb in history.ratings:
            rating = float(fb.rating)
            weight = rating - 3.0  # center around 3.0
            if weight > 0:
                total_pos_weight += weight
            movie_vec = self._movie_vectors.get(fb.movie_id)
            if movie_vec is None:
                continue
            for i in range(len(user_vec)):
                user_vec[i] += weight * movie_vec[i]

        # If no positive feedback, use all liked genres equally
        if total_pos_weight == 0:
            # Fallback: use genre counts from ratings >= 3.5
            for fb in history.ratings:
                if float(fb.rating) >= 3.5:
                    movie_vec = self._movie_vectors.get(fb.movie_id)
                    if movie_vec:
                        for i in range(len(user_vec)):
                            user_vec[i] += movie_vec[i]

        # Compute cosine similarity with all candidate movies
        # Only consider movies in the training catalog
        catalog = FrozenData().catalog()
        candidates = [(mid, vec) for mid, vec in self._movie_vectors.items()
                      if mid in catalog and mid not in rated_ids]

        # Compute user vector norm
        user_norm = sum(x * x for x in user_vec) ** 0.5
        if user_norm == 0:
            # No meaningful profile - return empty
            candidates = []

        scored = []
        for movie_id, vec in candidates:
            dot = sum(user_vec[i] * vec[i] for i in range(len(user_vec)))
            vec_norm = sum(x * x for x in vec) ** 0.5
            if vec_norm == 0 or user_norm == 0:
                score = 0.0
            else:
                score = dot / (user_norm * vec_norm)
            scored.append((movie_id, score))

        # Sort by score descending, then by movie_id ascending (tie-break)
        scored.sort(key=lambda x: (-x[1], int(x[0])))

        # Take top N
        top_n = scored[:request.limit]

        model_version = "content-genre-v1"
        items = []
        for rank, (movie_id, score) in enumerate(top_n, 1):
            # Build recall_sources: list of matching genres for explainability
            movie_genre_set = set(self._movie_genres.get(movie_id, []))
            # Find top contributing genres from user profile
            top_genres = []
            genre_scores = []
            for genre, idx in self._genre_index.items():
                if user_vec[idx] > 0 and genre in movie_genre_set:
                    genre_scores.append((genre, user_vec[idx]))
            genre_scores.sort(key=lambda x: -x[1])
            top_genres = [g for g, _ in genre_scores[:3]]

            items.append(RecommendationItem(
                movie_id=movie_id,
                rank=rank,
                algorithm=Algorithm.content,
                model_version=model_version,
                score=round(score, 6),
                recall_sources=top_genres if top_genres else None,
            ))

        return RecommendationResult(
            status="ready",
            algorithm=Algorithm.content,
            model_version=model_version,
            dataset_version=request.dataset_version,
            split_version=request.split_version,
            runtime_mode=request.runtime_mode,
            generated_at=datetime.now(timezone.utc),
            items=items,
        )
