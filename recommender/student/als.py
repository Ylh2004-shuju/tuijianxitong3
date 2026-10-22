"""Spark ALS（LFM 矩阵分解）业务推荐（实验三任务1）。

模型由 scripts/train_als.py 在冻结的 business/history_ratings.csv 上用
Spark MLlib ALS 显式评分训练（rank=16, regParam=0.1, maxIter=15, seed=42），
导出全部用户/电影隐向量。线上推理只用向量内积，不再连接 Spark：

  score(u, m) = dot(user_factors[u], item_factors[m])

历史用户 Top-10 = 目录中未评分电影按分数降序；同分按 movie_id 升序。
可解释性：给出与候选隐向量余弦最相近的已评分高分支种子电影。
"""
from datetime import datetime, timezone
from pathlib import Path

from ..business import read_history
from ..contracts import Algorithm, RecommendationItem, RecommendationRequest, RecommendationResult
from .common import empty_result, movie_title, read_artifact_gz, top_scored

MODEL_VERSION = "als-implicit-explicit-v1"


class ALSRecommender:
    model_manifest_path: str | Path

    def __init__(self, manifest_path: str | Path):
        self.model_manifest_path = Path(manifest_path)
        self._users: dict[str, list[float]] | None = None
        self._items: dict[str, list[float]] | None = None

    def _load(self) -> tuple[dict[str, list[float]], dict[str, list[float]]]:
        if self._users is None or self._items is None:
            base = self.model_manifest_path.parent
            self._users = read_artifact_gz(base / "user_factors.json.gz")["users"]
            self._items = read_artifact_gz(base / "item_factors.json.gz")["items"]
        return self._users, self._items

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        dot = norm_a = norm_b = 0.0
        for x, y in zip(a, b):
            dot += x * y
            norm_a += x * x
            norm_b += y * y
        if dot <= 0.0 or norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        return dot / ((norm_a ** 0.5) * (norm_b ** 0.5))

    def recommend(self, request: RecommendationRequest) -> RecommendationResult:
        users, items = self._load()
        vector = users.get(str(request.subject_id))
        history = read_history(request)
        rated = {feedback.movie_id for feedback in history.ratings}
        if not vector:
            # 无隐向量的用户（训练数据中没有该用户）：诚实返回空列表。
            return empty_result(request, Algorithm.als, MODEL_VERSION)

        seeds = sorted(((f.movie_id, float(f.rating)) for f in history.ratings),
                       key=lambda pair: (-pair[1], int(pair[0])))
        positive = [mid for mid, score in seeds if score >= 4.0][:5]

        scores: dict[str, float] = {}
        for movie_id, item_vector in items.items():
            if movie_id in rated:
                continue
            scores[movie_id] = sum(a * b for a, b in zip(vector, item_vector))

        items_out: list[RecommendationItem] = []
        for movie_id, score in top_scored(scores, request.limit):
            candidate = items[movie_id]
            best: tuple[float, str] | None = None
            for seed_id in positive:
                seed_vector = items.get(seed_id)
                if seed_vector is None:
                    continue
                similarity = self._cosine(candidate, seed_vector)
                if best is None or similarity > best[0]:
                    best = (similarity, seed_id)
            source = (f"与你看过的《{movie_title(best[1])}》隐向量相近" if best
                      else "LFM 隐向量内积预测")
            items_out.append(RecommendationItem(
                movie_id=movie_id,
                rank=len(items_out) + 1,
                algorithm=Algorithm.als,
                model_version=MODEL_VERSION,
                score=score,
                recall_sources=[source],
            ))

        return RecommendationResult(
            algorithm=Algorithm.als,
            model_version=MODEL_VERSION,
            dataset_version=request.dataset_version,
            split_version=request.split_version,
            runtime_mode=request.runtime_mode,
            generated_at=datetime.now(timezone.utc),
            items=items_out,
        )
