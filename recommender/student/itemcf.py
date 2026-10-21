"""Item-based collaborative filtering (实验二 任务3).

Similarity comes from real training interactions only: two movies are similar
when the same users rated both >= 4.0.  The similarity is cosine over that
binary co-occurrence matrix with three guards against data sparsity:

  * a pair needs at least ``min_cooccurrence`` shared users (3 by default);
  * each movie keeps only its top ``max_neighbours`` (30) partners;
  * movies without enough support simply have no neighbours and are never
    recommended as candidates, instead of receiving a noisy similarity.

A user's score for a candidate is ``sum(similarity * (rating - 3.0))`` over the
user's positive seeds.  Rated movies are always removed, and every
recommendation reports which seed movies produced it.
"""
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from ..business import read_history
from ..contracts import Algorithm, RecommendationItem, RecommendationRequest, RecommendationResult
from .common import empty_result, movie_title, read_artifact_gz, top_scored

MODEL_VERSION = "itemcf-cosine-v1"
POSITIVE_THRESHOLD = 4.0


class ItemCFRecommender:
    model_manifest_path: str | Path

    def __init__(self, manifest_path: str | Path):
        self.model_manifest_path = Path(manifest_path)
        self._neighbours: dict[str, tuple[tuple[str, float], ...]] | None = None

    def _load(self) -> dict[str, tuple[tuple[str, float], ...]]:
        if self._neighbours is None:
            payload = read_artifact_gz(self.model_manifest_path.parent / "item_neighbors.json.gz")
            self._neighbours = {
                movie_id: tuple((neighbour, float(similarity)) for neighbour, similarity in values)
                for movie_id, values in payload["items"].items()
            }
        return self._neighbours

    def recommend(self, request: RecommendationRequest) -> RecommendationResult:
        history = read_history(request)
        rated = {feedback.movie_id for feedback in history.ratings}
        seeds = [(feedback.movie_id, float(feedback.rating) - 3.0)
                 for feedback in history.ratings if float(feedback.rating) >= POSITIVE_THRESHOLD]
        if not seeds:
            # No positive seed means no honest collaborative signal: return an
            # empty list rather than copying a global chart.
            return empty_result(request, Algorithm.itemcf, MODEL_VERSION)

        neighbours = self._load()
        scores: dict[str, float] = defaultdict(float)
        evidence: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for seed_id, weight in seeds:
            for candidate_id, similarity in neighbours.get(seed_id, ()):
                if candidate_id in rated:
                    continue
                contribution = similarity * weight
                scores[candidate_id] += contribution
                evidence[candidate_id][seed_id] += contribution

        items: list[RecommendationItem] = []
        for movie_id, score in top_scored(scores, request.limit):
            reasons = sorted(evidence[movie_id].items(), key=lambda pair: (-pair[1], int(pair[0])))[:2]
            items.append(RecommendationItem(
                movie_id=movie_id,
                rank=len(items) + 1,
                algorithm=Algorithm.itemcf,
                model_version=MODEL_VERSION,
                score=score,
                recall_sources=[f"因你看过《{movie_title(seed)}》" for seed, _ in reasons],
            ))

        return RecommendationResult(
            algorithm=Algorithm.itemcf,
            model_version=MODEL_VERSION,
            dataset_version=request.dataset_version,
            split_version=request.split_version,
            runtime_mode=request.runtime_mode,
            generated_at=datetime.now(timezone.utc),
            items=items,
        )
