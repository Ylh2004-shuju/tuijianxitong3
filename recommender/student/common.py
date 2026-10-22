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
    """movie_id -> {"title": str, "genres": tuple[str, ...], "year": int | None}."""
    table: dict[str, dict] = {}
    for row in frozen().rows("movies.csv"):
        raw = row["genres"] or ""
        genres = () if raw in {"", "(no genres listed)"} else tuple(g for g in raw.split("|") if g)
        year = None
        raw_year = (row["release_year"] or "").strip()
        if raw_year.isdigit():
            year = int(raw_year)
        table[row["movie_id"]] = {"title": row["movielens_title"], "genres": genres, "year": year}
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


# --------------------------------------------------------------------------- #
# content features: IDF 加权的类型向量 + 年代（内容推荐改进用）
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def genre_idf() -> dict[str, float]:
    """IDF 权重：出现越少类型的区分度越高。"""
    table = movie_table()
    total = len(table)
    frequency: dict[str, int] = {}
    for meta in table.values():
        for genre in meta["genres"]:
            frequency[genre] = frequency.get(genre, 0) + 1
    return {genre: math.log(total / max(count, 1)) + 1.0 for genre, count in sorted(frequency.items())}


def idf_genre_vector(genres: tuple[str, ...]) -> dict[str, float]:
    weights = genre_idf()
    return {genre: weights.get(genre, 1.0) for genre in genres}


def idf_cosine(profile: dict[str, float], profile_norm_value: float, movie_id: str) -> float:
    """IDF 加权的类型余弦：profile 与 movie 都以 IDF 权重加权。"""
    if profile_norm_value == 0.0:
        return 0.0
    vector = idf_genre_vector(movie_genres(movie_id))
    if not vector:
        return 0.0
    dot = sum(weight * profile.get(genre, 0.0) for genre, weight in vector.items())
    if dot <= 0.0:
        return 0.0
    item_norm = math.sqrt(sum(weight * weight for weight in vector.values()))
    return dot / (profile_norm_value * item_norm)


def movie_decade(movie_id: str) -> str:
    year = movie_table().get(movie_id, {}).get("year")
    return "unknown" if year is None else f"{year // 10 * 10}s"


# --------------------------------------------------------------------------- #
# MMR 多样性重排（热门榜改进用）
# --------------------------------------------------------------------------- #
def genre_jaccard(left: tuple[str, ...], right: tuple[str, ...]) -> float:
    if not left or not right:
        return 0.0
    a, b = set(left), set(right)
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def mmr_select(entries: list[tuple[str, float]], limit: int, genres_of,
               lambda_relevance: float = 0.7, pool: int = 200) -> list[tuple[str, float]]:
    """MMR 贪心重排：λ·相关性 − (1−λ)·与已选项的最大类型重叠。

    entries 必须已按分数降序、同分按 movie_id 升序排好。
    """
    candidates = entries[:pool]
    if not candidates:
        return []
    values = [score for _, score in candidates]
    lowest, highest = min(values), max(values)
    spread = highest - lowest

    def relevance(score: float) -> float:
        return (score - lowest) / spread if spread > 0 else 1.0

    selected: list[tuple[str, float]] = []
    chosen_genres: list[tuple[str, ...]] = []
    remaining = list(candidates)
    while remaining and len(selected) < limit:
        best_index, best_key = 0, None
        for index, (movie_id, score) in enumerate(remaining):
            genres = genres_of(movie_id)
            overlap = max((genre_jaccard(genres, prior) for prior in chosen_genres), default=0.0)
            key = (round(lambda_relevance * relevance(score) - (1 - lambda_relevance) * overlap, 6),
                   -int(movie_id))
            if best_key is None or key > best_key:
                best_index, best_key = index, key
        movie_id, score = remaining.pop(best_index)
        selected.append((movie_id, score))
        chosen_genres.append(genres_of(movie_id))
    return selected


# 展示分尺度：预测评分落在 [1, 5]，且 [1.5, 4.5] 区间内保持原值
RATING_MIN, RATING_MAX = 1.0, 5.0
RATING_KNEE_LOW, RATING_KNEE_HIGH = 1.5, 4.5


def squash_rating(raw: float) -> float:
    """把预测评分单调压缩到 (1, 5) 区间内。

    中间区间 [1.5, 4.5] 保持原值，两端用 1 - 1/(1+t) 渐近压缩。
    严格单调递增，因此**不改变任何排序**，也不会产生并列分。
    """
    value = float(raw)
    if not math.isfinite(value):
        return min(max(raw, RATING_MIN), RATING_MAX)
    if RATING_KNEE_LOW <= value <= RATING_KNEE_HIGH:
        return value
    if value > RATING_KNEE_HIGH:
        span = RATING_MAX - RATING_KNEE_HIGH
        t = (value - RATING_KNEE_HIGH) / span
        return RATING_KNEE_HIGH + span * (1.0 - 1.0 / (1.0 + t))
    span = RATING_KNEE_LOW - RATING_MIN
    t = (RATING_KNEE_LOW - value) / span
    return RATING_KNEE_LOW - span * (1.0 - 1.0 / (1.0 + t))


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
