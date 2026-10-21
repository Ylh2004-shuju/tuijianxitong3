"""Shared, read-only feature access and deterministic ranking helpers.

Every student provider reuses this module so that:
  * the frozen package is opened once per process (no per-request checksum work),
  * the movie catalog / genre index is built once,
  * ranking always rounds the score *before* sorting, which keeps the DTO rule
    "equal scores must be ordered by ascending numeric movie_id" true even when
    two raw scores differ by less than 1e-6.
"""
from __future__ import annotations

import gzip
import json
import math
from functools import lru_cache
from pathlib import Path

from ..data import FrozenData


@lru_cache(maxsize=1)
def frozen() -> FrozenData:
    return FrozenData()


@lru_cache(maxsize=1)
def movie_table() -> dict[str, dict]:
    """movie_id -> {"title": str, "genres": tuple[str, ...]}."""
    table: dict[str, dict] = {}
    for row in frozen().rows("movies.csv"):
        raw = row["genres"] or ""
        genres = () if raw in {"", "(no genres listed)"} else tuple(g for g in raw.split("|") if g)
        table[row["movie_id"]] = {"title": row["movielens_title"], "genres": genres}
    return table


@lru_cache(maxsize=1)
def catalog() -> frozenset[str]:
    return frozenset(frozen().catalog())


@lru_cache(maxsize=1)
def genre_index() -> dict[str, int]:
    genres = sorted({g for meta in movie_table().values() for g in meta["genres"]})
    return {genre: position for position, genre in enumerate(genres)}


def movie_title(movie_id: str) -> str:
    return movie_table().get(movie_id, {}).get("title") or f"电影 #{movie_id}"


def movie_genres(movie_id: str) -> tuple[str, ...]:
    return movie_table().get(movie_id, {}).get("genres", ())


# --------------------------------------------------------------------------- #
# artifacts
# --------------------------------------------------------------------------- #
def read_artifact(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_artifact_gz(path: str | Path) -> dict:
    with gzip.open(Path(path), "rt", encoding="utf-8") as stream:
        return json.load(stream)


# --------------------------------------------------------------------------- #
# scoring
# --------------------------------------------------------------------------- #
def genre_profile(weights: dict[str, float]) -> list[float]:
    """Weighted one-hot genre profile; the caller decides what each weight means."""
    index = genre_index()
    vector = [0.0] * len(index)
    table = movie_table()
    for movie_id, weight in weights.items():
        meta = table.get(movie_id)
        if meta is None or weight == 0.0:
            continue
        for genre in meta["genres"]:
            vector[index[genre]] += weight
    return vector


def profile_norm(profile: list[float]) -> float:
    return math.sqrt(sum(value * value for value in profile))


def genre_cosine(profile: list[float], profile_norm_value: float, movie_id: str) -> float:
    """Cosine between a genre profile and one movie's one-hot genre vector."""
    genres = movie_genres(movie_id)
    if not genres or profile_norm_value == 0.0:
        return 0.0
    index = genre_index()
    dot = 0.0
    for genre in genres:
        position = index.get(genre)
        if position is not None:
            dot += profile[position]
    if dot == 0.0:
        return 0.0
    return dot / (profile_norm_value * math.sqrt(len(genres)))


def top_genres(profile: list[float], limit: int = 3) -> list[str]:
    ranked = sorted(
        ((genre, profile[position]) for genre, position in genre_index().items() if profile[position] > 0),
        key=lambda pair: (-pair[1], pair[0]),
    )
    return [genre for genre, _ in ranked[:limit]]


def top_scored(scores: dict[str, float], limit: int) -> list[tuple[str, float]]:
    """Keep approved catalog ids, drop non-finite values, round, then sort."""
    approved = catalog()
    cleaned: dict[str, float] = {}
    for movie_id, value in scores.items():
        if movie_id not in approved:
            continue
        value = float(value)
        if not math.isfinite(value):
            continue
        cleaned[movie_id] = round(value, 6)
    ordered = sorted(cleaned.items(), key=lambda pair: (-pair[1], int(pair[0])))
    return ordered[:limit]


def empty_result(request, algorithm, model_version: str):
    """Build a legal empty RecommendationResult (items=[]) for the given request."""
    from datetime import datetime, timezone

    from ..contracts import RecommendationResult

    return RecommendationResult(
        algorithm=algorithm,
        model_version=model_version,
        dataset_version=request.dataset_version,
        split_version=request.split_version,
        runtime_mode=request.runtime_mode,
        generated_at=datetime.now(timezone.utc),
        items=[],
    )
