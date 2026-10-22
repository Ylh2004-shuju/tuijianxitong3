"""实验四 S4：推荐解释的**证据链**（只来自真实数据与真实模型产物）。

设计原则：
1. 这里产出的每一条证据都能在数据库、冻结数据包或 models/ 下的模型产物里查到出处；
2. 大模型只能在候选证据里挑引用，不能自己编剧情、演员、奖项；
3. 后端校验模型返回的 evidence 必须是候选 id 的子集，否则整条解释作废（502），
   宁可不返回，也不把幻觉当成解释。
"""
from __future__ import annotations

import json

from recommender.contracts import Algorithm, RecommendationItem
from recommender.student.common import (genre_idf, genre_index, movie_decade, movie_genres,
                                        movie_table, movie_title, read_artifact,
                                        read_artifact_gz, top_genres)

from .config import ROOT

MODELS = ROOT / "models" / "business"
# 同一份模型产物只解析一次：item_neighbors 有几十万条边，popular/als 也不小
_CACHE: dict[str, object] = {}


def _cached(key: str, loader):
    if key not in _CACHE:
        _CACHE[key] = loader()
    return _CACHE[key]


def _itemcf_neighbours() -> dict[str, dict[str, float]]:
    def load():
        payload = read_artifact_gz(MODELS / "itemcf-v1" / "item_neighbors.json.gz")
        # 产物格式：{movie_id: [[neighbour, similarity], ...]}
        return {movie: {str(pair[0]): float(pair[1]) for pair in partners if float(pair[1]) > 0}
                for movie, partners in payload.get("items", {}).items()}
    return _cached("itemcf", load)


def _popular_stats() -> dict[str, dict]:
    def load():
        payload = read_artifact(MODELS / "popular-v1" / "popular_top.json")
        return {str(entry["movie_id"]): entry for entry in payload.get("items", [])}
    return _cached("popular", load)


def _als_biases() -> dict:
    return _cached("als_biases", lambda: read_artifact_gz(MODELS / "als-v1" / "biases.json.gz"))


def movie_line(movie_id: str) -> str:
    meta = movie_table().get(movie_id)
    if meta is None:
        return f"电影 #{movie_id}"
    parts = [f"《{meta['title']}》"]
    if meta.get("year"):
        parts.append(f"{meta['year']} 年")
    if meta.get("genres"):
        parts.append("类型：" + "、".join(meta["genres"]))
    return " · ".join(parts)


def movie_name(movie_id: str) -> str:
    """带书名号的片名，用于拼进句子里（movie_line 用于独立描述）。"""
    return f"《{movie_title(movie_id)}》"


def _preferred_genres(ratings: dict[str, float], limit: int = 3) -> list[str]:
    """与 content-feature-v2 完全一致：按 (评分 − 该用户均值) 加权，再乘类型 IDF。"""
    if not ratings:
        return []
    user_mean = sum(ratings.values()) / len(ratings)
    idf = genre_idf()
    profile: dict[str, float] = {}
    for movie_id, rating in ratings.items():
        weight = rating - user_mean
        if weight == 0.0:
            continue
        for genre in movie_genres(movie_id):
            profile[genre] = profile.get(genre, 0.0) + weight * idf.get(genre, 1.0)
    index = genre_index()
    ordered = [0.0] * len(index)
    for genre, weight in profile.items():
        position = index.get(genre)
        if position is not None:
            ordered[position] = weight
    return top_genres(ordered, limit)


def build_evidence(algorithm: Algorithm, movie_id: str, item: RecommendationItem,
                   ratings: dict[str, float]) -> dict[str, str]:
    """返回 {evidence_id: 中文描述}；key 是给大模型引用的稳定 id，value 是给人看的事实。"""
    evidence: dict[str, str] = {}

    # 1. 被推荐电影本身（元数据，与算法无关）
    evidence[f"movie:{movie_id}"] = "被推荐的电影 " + movie_line(movie_id)

    # 2. 这位观众自己的真实高分记录（最多 3 条，分数高的在前）
    top_rated = sorted(ratings.items(), key=lambda pair: (-pair[1], int(pair[0])))[:3]
    for seed_id, rating in top_rated:
        evidence[f"rated:{seed_id}"] = f"你给{movie_name(seed_id)}打过 {rating:.1f} 分"
    if ratings:
        evidence["history:size"] = f"你共有 {len(ratings)} 条历史评分"

    # 3. 算法自身的依据
    if algorithm is Algorithm.itemcf:
        neighbours = _itemcf_neighbours()
        matches: list[tuple[str, float, float]] = []
        for seed_id, rating in ratings.items():
            if rating < 4.0:
                continue
            similarity = neighbours.get(seed_id, {}).get(movie_id)
            if similarity:
                matches.append((seed_id, rating, similarity))
        matches.sort(key=lambda row: (-row[2], int(row[0])))
        for seed_id, rating, similarity in matches[:2]:
            evidence[f"sim:{seed_id}>{movie_id}"] = (
                f"ItemCF 共现相似度 {similarity:.2f}：{movie_name(seed_id)}（你打过 {rating:.1f} 分）"
                f"与{movie_name(movie_id)}被同一批用户同时打了 4 分以上")
        if not matches:
            evidence["itemcf:no_seed"] = "你看过的电影（≥4 分）里没有与它产生共现的邻居"

    elif algorithm is Algorithm.content:
        target_genres = movie_genres(movie_id)
        if target_genres:
            evidence[f"genres:{movie_id}"] = "它的类型：" + "、".join(target_genres)
        preferred = _preferred_genres(ratings)
        if preferred:
            evidence["pref:genres"] = "你的类型偏好（按你的评分偏离你均值的加权）：" + "、".join(preferred)
            overlap = [genre for genre in preferred if genre in target_genres]
            if overlap:
                evidence["match:genres"] = "与你的偏好重合的类型：" + "、".join(overlap)
        decade = movie_decade(movie_id)
        if decade != "unknown":
            evidence[f"decade:{movie_id}"] = f"它上映于 {decade} 年代"

    elif algorithm is Algorithm.popular:
        entry = _popular_stats().get(movie_id)
        if entry is not None:
            evidence[f"popular:{movie_id}"] = (
                f"全站有 {int(entry['positive_count'])} 人给它打了 4 分以上，"
                f"平均 {float(entry['mean_rating']):.2f} 分，共 {int(entry['rating_count'])} 人评分")
        evidence["popular:note"] = "热门榜与个人偏好无关，排序依据是高分人数 + 类型多样性重排"

    elif algorithm is Algorithm.als:
        biases = _als_biases()
        mu = float(biases.get("mu", 0.0))
        # 用户/电影偏置的 key 在模型里是字符串
        evidence["als:baseline"] = f"ALS 基线：全局均值 {mu:.2f} + 用户偏置 + 电影偏置 + 隐因子内积"
        if item.score is not None:
            evidence[f"als:pred:{movie_id}"] = f"模型对该片的预测评分（压缩后）{float(item.score):.2f} 分"

    elif algorithm is Algorithm.fusion:
        ranks = item.recall_ranks or {}
        if ranks:
            detail = "、".join(f"{name} 第 {position} 名" for name, position in sorted(ranks.items(), key=lambda kv: kv[1]))
            evidence[f"fusion:{movie_id}"] = f"它在各成员算法的名单里：{detail}（RRF 倒数排名融合）"

    # 4. 排序位置
    if item.score is not None:
        evidence[f"rank:{movie_id}"] = f"该算法把它排在第 {item.rank} 名，模型得分 {float(item.score):.4f}"
    else:
        evidence[f"rank:{movie_id}"] = f"该算法把它排在第 {item.rank} 名"
    return evidence


ALGORITHM_BRIEF = {
    Algorithm.content: "内容推荐：只用电影类型/年代元数据 + 这位观众自己的评分，不使用其他人的行为",
    Algorithm.itemcf: "ItemCF：用全站用户的评分共现，找出与这位观众喜欢过的电影相似的电影",
    Algorithm.als: "ALS 矩阵分解：隐语义因子 + 用户/电影偏置基线，拟合预测评分",
    Algorithm.popular: "热门榜：全站高分人数排序再做类型多样性重排，非个性化",
    Algorithm.fusion: "多算法融合：把热门、内容、ItemCF、ALS 四份名单按倒数排名（RRF）融合",
    Algorithm.cold_start: "冷启动：无行为时用全站热门兜底，有收藏或高分评分后切到类型偏好",
}


def prompt_payload(algorithm: Algorithm, movie_id: str, item: RecommendationItem,
                   evidence: dict[str, str]) -> str:
    return json.dumps({
        "movie": movie_title(movie_id),
        "algorithm": algorithm.value,
        "algorithm_how_it_works": ALGORITHM_BRIEF.get(algorithm, ""),
        "model_version": item.model_version,
        "rank": item.rank,
        "candidate_evidence": [{"id": key, "fact": value} for key, value in evidence.items()],
    }, ensure_ascii=False)
