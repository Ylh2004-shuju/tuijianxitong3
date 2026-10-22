from typing import Annotated

from fastapi import APIRouter, Query, Request
from pydantic import Field
from sqlalchemy import text

from recommender.contracts import Algorithm, AlgorithmNotImplemented, RecommendationRequest, RecommendationResult, PROVIDERS, recommend
from recommender.data import ContractError
from .config import DATASET, SPLIT
from .errors import APIError
from .schemas import IdPath, Input, identifier
from .security import ActorDependency

router = APIRouter(tags=["学生算法预留边界"])


class RecommendationQuery(Input):
    limit: int = Field(default=10, ge=1, le=50)


def invoke(algorithm: Algorithm, kind: str, uid: int, limit: int):
    request = RecommendationRequest(kind, uid, limit, "business", DATASET, SPLIT)
    try:
        return recommend(algorithm, request)
    except AlgorithmNotImplemented:
        raise APIError(501, "ALGORITHM_NOT_IMPLEMENTED", "此推荐算法留给学生实现", {
            "status": "not_implemented", "algorithm": algorithm.value, "subject_key": kind + ":" + str(uid),
            "dataset_version": DATASET, "split_version": SPLIT, "runtime_mode": "business",
            "model_version": None, "batch_id": None, "items": []}) from None
    except ContractError as exc:
        raise APIError(503 if exc.code in {"MODEL_BINDING_INVALID", "DATA_IDENTITY_INVALID", "DATA_MODE_INVALID"} else 500,
            exc.code, str(exc)) from None


@router.get("/experiments/algorithms")
def algorithms():
    return {"items": [{"algorithm": algorithm.value, "status": "registered" if algorithm in PROVIDERS else "not_implemented"} for algorithm in Algorithm]}


@router.get("/experiments/evaluations")
def evaluations():
    """统一协议离线评价结果（实验三）：validation 分区 Precision@10 / Recall@10。"""
    import json
    from pathlib import Path

    summary_path = Path(__file__).resolve().parents[2] / "models" / "evaluation" / "results" / "summary.json"
    if not summary_path.is_file():
        raise APIError(501, "EVALUATION_NOT_IMPLEMENTED", "评价流程留给学生实现；业务API不连接evaluation数据库", {
            "status": "not_implemented", "metrics": None, "dataset_version": DATASET, "split_version": SPLIT})
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    return {
        "dataset_version": summary["dataset_version"], "split_version": summary["split_version"],
        "partition": summary["partition"], "user_count": summary["user_count"],
        "positive_threshold": summary["positive_threshold"], "top_k": summary["top_k"],
        "items": [
            {"algorithm": algo, "model_version": row["model_version"],
             "precision_at_10": row["precision_at_10"], "recall_at_10": row["recall_at_10"],
             "total_hits": row["total_hits"], "users_with_items": row["users_with_items"],
             "users_empty": row["users_empty"], "coverage_movies": row["coverage_movies"],
             "status": row["status"]}
            for algo, row in sorted(summary["algorithms"].items(),
                                    key=lambda pair: -pair[1]["recall_at_10"])],
    }


@router.get("/me/recommendations/{algorithm}", response_model=RecommendationResult)
def my_recommendations(algorithm: Algorithm, actor: ActorDependency, query: Annotated[RecommendationQuery, Query()]):
    return invoke(algorithm, "app", actor.account_id, query.limit)


@router.get("/demo/history-users/{ml_user_id}/recommendations/{algorithm}", response_model=RecommendationResult)
def history_recommendations(ml_user_id: IdPath, algorithm: Algorithm, request: Request,
                            query: Annotated[RecommendationQuery, Query()]):
    uid = identifier(ml_user_id)
    with request.app.state.db.connection() as conn:
        if conn.execute(text("SELECT ml_user_id FROM history_users WHERE ml_user_id=:id"), {"id": uid}).first() is None:
            raise APIError(404, "HISTORY_USER_NOT_FOUND", "历史匿名用户不存在")
    return invoke(algorithm, "ml", uid, query.limit)
