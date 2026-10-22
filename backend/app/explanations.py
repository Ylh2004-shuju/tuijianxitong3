"""实验四 S4：大模型个性化推荐解释。

流程：真实推荐结果 → 真实证据链 → 大模型（只能引用证据）→ 后端校验 → 返回。
没有配置密钥时如实返回 503，绝不用模板文案冒充"AI 解释"。
"""
from datetime import datetime, timezone
from typing import Literal

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import Field

from recommender.business import read_history
from recommender.contracts import Algorithm, RecommendationRequest
from recommender.data import ContractError
from .config import DATASET, SPLIT
from .errors import APIError
from .explain import ALGORITHM_BRIEF, build_evidence, prompt_payload
from .experiments import invoke
from .llm import chat_json
from .schemas import Input
from .security import optional_actor

router = APIRouter(tags=["学生算法预留边界"])

# 解释用比展示用取深一点的名单，避免用户想解释第 11~20 名时找不到
EXPLAIN_DEPTH = 20
# 同一条解释短期重复点击直接复用，避免重复计费与等待
_CACHE: dict[tuple, dict] = {}
_CACHE_LIMIT = 256

SYSTEM_PROMPT = (
    "你是电影推荐系统的解释助手。你要向一位观众解释：为什么系统把这部电影推荐给他。\n"
    "硬性规则：\n"
    "1. 只能使用 candidate_evidence 里给出的证据，严禁补充证据以外的任何信息，"
    "尤其不能编造演员、导演、剧情、奖项、豆瓣评分、上映国家、观影热度等。\n"
    "2. 必须严格输出 JSON 对象，格式 {\"reason\": \"...\", \"evidence\": [\"id1\", \"id2\"]}，不要输出其他内容。\n"
    "3. reason 用简体中文，2 到 3 句话，口语化，直接称呼观众为「你」；"
    "要点明算法依据的是哪一类数据（例如「和你一样喜欢 X 的人」「你偏好的类型」「全站高分」）。\n"
    "4. evidence 只能从 candidate_evidence 的 id 里选，最多 4 个，按重要性排序；"
    "不要重复写电影名，也不要输出证据的全文。\n"
    "5. 如果证据不足以支撑个性化解释，就如实说明这是非个性化的热门推荐，不要硬编理由。"
)

# 校验失败后追加的重试提示：只补规则，不补事实
RETRY_HINT = "上一次输出不合法。请重新输出同样的 JSON 结构：reason 为 2-3 句中文，evidence 只含 candidate_evidence 中出现过的 id。"


class ExplanationInput(Input):
    subject_kind: Literal["app", "ml"]
    subject_id: int = Field(ge=1, le=2**64 - 1)
    algorithm: Algorithm
    movie_id: str = Field(min_length=1, max_length=20, pattern=r"^[1-9][0-9]{0,19}$")


def _validate(answer: dict, allowed: set[str]) -> tuple[str, list[str]] | None:
    """模型输出必须满足：reason 是非空中文短文本，evidence 是候选 id 的子集。"""
    reason = answer.get("reason")
    if not isinstance(reason, str) or not 8 <= len(reason.strip()) <= 500:
        return None
    raw = answer.get("evidence")
    if raw is None:
        raw = []
    if not isinstance(raw, list) or len(raw) > 4:
        return None
    ids: list[str] = []
    for value in raw:
        if not isinstance(value, str) or value not in allowed:
            return None
        if value not in ids:
            ids.append(value)
    return reason.strip(), ids


@router.get("/explanations/status")
def explanation_status(request: Request):
    """前端据此决定是否显示「AI 解释」按钮：没配密钥就不显示，而不是点了才报错。"""
    settings = request.app.state.settings
    return {"configured": settings.llm_api_key is not None, "model": settings.llm_model}


@router.post("/explanations")
def explain(payload: ExplanationInput, request: Request,
            actor: Annotated[object | None, Depends(optional_actor)] = None):
    settings = request.app.state.settings
    if payload.subject_kind == "app":
        # 注册账号只能解释自己的推荐；MovieLens 历史用户是只读演示数据，允许匿名查看
        if actor is None:
            raise APIError(401, "AUTHENTICATION_REQUIRED", "请先登录后为自己的推荐生成解释")
        if actor.account_id != payload.subject_id:
            raise APIError(403, "SUBJECT_FORBIDDEN", "只能为自己生成推荐解释")

    result = invoke(payload.algorithm, payload.subject_kind, payload.subject_id, EXPLAIN_DEPTH)
    item = next((entry for entry in result.items if entry.movie_id == payload.movie_id), None)
    if item is None:
        raise APIError(404, "EXPLANATION_TARGET_NOT_FOUND",
                       "该电影不在当前推荐名单前 %d 名内，无法解释" % EXPLAIN_DEPTH)

    cache_key = (payload.subject_kind, payload.subject_id, payload.algorithm.value,
                 payload.movie_id, result.model_version)
    cached = _CACHE.get(cache_key)
    if cached is not None:
        return {**cached, "cached": True}

    query = RecommendationRequest(payload.subject_kind, payload.subject_id, EXPLAIN_DEPTH,
                                  "business", DATASET, SPLIT)
    try:
        history = read_history(query, database=request.app.state.db)
    except ContractError as exc:
        raise APIError(404 if exc.code == "USER_NOT_FOUND" else 500, exc.code, str(exc)) from None
    ratings = {feedback.movie_id: float(feedback.rating) for feedback in history.ratings}

    evidence = build_evidence(payload.algorithm, payload.movie_id, item, ratings)
    user_prompt = (
        prompt_payload(payload.algorithm, payload.movie_id, item, evidence)
        + "\n一句话背景：" + ALGORITHM_BRIEF.get(payload.algorithm, "")
    )
    answer = chat_json(SYSTEM_PROMPT, user_prompt, settings)
    parsed = _validate(answer, set(evidence))
    if parsed is None:
        # 给一次纠错机会；仍然不合法就如实报错，绝不把未校验内容返回给前端
        answer = chat_json(SYSTEM_PROMPT, user_prompt + "\n" + RETRY_HINT, settings, temperature=0.0)
        parsed = _validate(answer, set(evidence))
    if parsed is None:
        raise APIError(502, "LLM_BAD_RESPONSE", "大模型未按要求引用证据，解释已作废")
    reason, used = parsed

    body = {
        "subject_key": f"{payload.subject_kind}:{payload.subject_id}",
        "algorithm": payload.algorithm.value,
        "model_version": result.model_version,
        "movie_id": payload.movie_id,
        "rank": item.rank,
        "reason": reason,
        "evidence": used,
        "evidence_detail": {key: evidence[key] for key in used},
        "llm_model": settings.llm_model,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cached": False,
    }
    if len(_CACHE) >= _CACHE_LIMIT:
        _CACHE.clear()
    _CACHE[cache_key] = dict(body)
    return body
