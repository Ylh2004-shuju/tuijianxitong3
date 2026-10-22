"""Spark ALS（LFM 矩阵分解）业务推荐（实验三任务1 + 改进版）。

模型由 scripts/train_als.py 在冻结的 business/history_ratings.csv 上用
Spark MLlib ALS 训练（rank=16, regParam=0.1, maxIter=15, seed=42），
拟合目标是去基线后的残差：

    residual(u, i) = r(u, i) − μ − b_u − b_i
    r̂(u, i)       = μ + b_u + b_i + p_u · q_i

线上推理只用向量内积加基线，不再连接 Spark：

  * μ / b_u / b_i 承担"全局平均分、用户打分松紧、电影口碑"，
    隐向量只负责个体差异，长尾电影不再靠少数评分把内积推到 5 分制以外；
  * 候选额外要求该电影在训练数据里至少有 20 条评分（曝光度过滤），
    避免推荐几乎没人看过的电影；
  * 输出前用单调压缩把预测分收进 (1, 5) 评分尺度（中段 [1.5, 4.5] 保持原值），
    排序与原始预测分严格一致，因此不会再出现 6.x 这类越界的"预测评分"。

历史用户 Top-10 = 通过曝光过滤的未评分电影按预测分降序；同分按 movie_id 升序。
可解释性：给出与候选隐向量余弦最相近的已评分高分支种子电影。
"""
from datetime import datetime, timezone
from pathlib import Path

from ..business import read_history
from ..contracts import Algorithm, RecommendationItem, RecommendationRequest, RecommendationResult
from .common import (empty_result, movie_title, read_artifact_gz, squash_rating,
                    top_scored)

MODEL_VERSION = "als-baseline-residual-v2"
# 与训练脚本保持一致：候选必须至少有这么多条训练评分
MIN_EXPOSURE = 20


class ALSRecommender:
    model_manifest_path: str | Path

    def __init__(self, manifest_path: str | Path):
        self.model_manifest_path = Path(manifest_path)
        self._users: dict[str, list[float]] | None = None
        self._items: dict[str, list[float]] | None = None
        self._biases: dict | None = None

    def _load(self) -> tuple[dict[str, list[float]], dict[str, list[float]], dict]:
        if self._biases is None:
            base = self.model_manifest_path.parent
            self._users = read_artifact_gz(base / "user_factors.json.gz")["users"]
            self._items = read_artifact_gz(base / "item_factors.json.gz")["items"]
            self._biases = read_artifact_gz(base / "biases.json.gz")
        return self._users, self._items, self._biases

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
        users, items, biases = self._load()
        user_id = str(request.subject_id)
        vector = users.get(user_id)
        history = read_history(request)
        rated = {feedback.movie_id for feedback in history.ratings}
        if vector is None:
            # 无隐向量的用户（训练数据中没有该用户）：诚实返回空列表。
            return empty_result(request, Algorithm.als, MODEL_VERSION)

        mu = float(biases.get("mu", 0.0))
        user_bias = float(biases.get("users", {}).get(user_id, 0.0))
        item_bias = biases.get("items", {})
        counts = biases.get("item_counts", {})
        min_exposure = int(biases.get("min_exposure", MIN_EXPOSURE))

        seeds = sorted(((f.movie_id, float(f.rating)) for f in history.ratings),
                       key=lambda pair: (-pair[1], int(pair[0])))
        positive = [mid for mid, score in seeds if score >= 4.0][:5]

        scores: dict[str, float] = {}
        for movie_id, item_vector in items.items():
            if movie_id in rated:
                continue
            if counts and counts.get(movie_id, 0) < min_exposure:
                continue
            raw = mu + user_bias + float(item_bias.get(movie_id, 0.0)) + sum(
                a * b for a, b in zip(vector, item_vector))
            # 预测分压缩到 (1,5)：严格单调，排序等价于原始预测分，且不产生并列分
            scores[movie_id] = squash_rating(raw)

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
