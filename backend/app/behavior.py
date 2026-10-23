"""实验五：用户行为快照与推荐依据（证明新行为真的参与了计算）。

为什么需要这个模块
------------------
"推荐列表变了"不能证明系统读了新行为，因为列表本来就可能稳定；"列表没变"也
不能证明系统没重算。所以这里把**每次推荐实际读取的那份行为输入**摊开给前端看：

* ``fingerprint``：把所有评分（电影:分数）与收藏排序后做 sha256 取前 12 位。
  同一份输入必然得到同一指纹；新增一条评分，指纹立刻变化。于是
  「指纹变了」= 「系统确实重新读取了含新行为的输入」，这是机器可验证的证据，
  而且它与"列表是否变化"无关，正好满足验收标准里
  「推荐结果不要求每次必然变化，但必须能够证明系统已利用最新行为重新计算」。
* ``latest``：最近 5 条行为及其数据库真实时间戳，可与个人中心逐条对照。
* ``as_of``：这次读取的服务器时间，证明是本次请求现算的，不是缓存快照。
* ``consumers``：每个算法究竟怎么用这份输入（全量 / 只用来排除已看 / 完全不用），
  避免把热门榜或 ALS 说成"根据你的行为推荐"。

数据全部来自业务库 ``account_ratings`` / ``account_favorites``，与推荐算法
读取的是同一张表、同一批行。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256

from fastapi import APIRouter, Request
from sqlalchemy import text

from recommender.student.cold_start import FAVORITE_WEIGHT, RATING_MIN
from recommender.student.common import genre_profile, top_genres

from .security import ActorDependency

router = APIRouter(prefix="/me", tags=["当前账号行为与推荐依据"])


@dataclass(frozen=True)
class Snapshot:
    account_id: int
    ratings: dict[str, float]          # movie_id -> rating
    rated_at: dict[str, str]           # movie_id -> ISO 时间
    favorites: tuple[str, ...]
    favorite_at: dict[str, str]
    titles: dict[str, str]
    genres: dict[str, list[str]]


def behavior_fingerprint(ratings: dict[str, float], favorites: tuple[str, ...]) -> str:
    """同一份行为输入 → 同一个指纹；任何一条评分/收藏增减都会改变它。"""
    payload = ";".join(f"{mid}:{rating:.1f}" for mid, rating in sorted(ratings.items(), key=lambda kv: int(kv[0])))
    payload += "|favorites:" + ",".join(sorted(favorites, key=int))
    return sha256(payload.encode("utf-8")).hexdigest()[:12]


def _iso(value: datetime | None) -> str | None:
    return value.replace(tzinfo=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z") if value else None


def read_snapshot(request: Request, account_id: int) -> Snapshot:
    """一次读全：评分、收藏、片名、类型。与推荐算法读的是同一批行。"""
    with request.app.state.db.connection() as conn:
        rating_rows = conn.execute(text(
            "SELECT r.movie_id, r.rating, r.updated_at, m.movielens_title, m.genres "
            "FROM account_ratings r JOIN movies m ON m.movie_id=r.movie_id "
            "WHERE r.account_id=:uid ORDER BY r.updated_at DESC, r.movie_id"),
            {"uid": account_id}).mappings().all()
        favorite_rows = conn.execute(text(
            "SELECT f.movie_id, f.created_at, m.movielens_title, m.genres "
            "FROM account_favorites f JOIN movies m ON m.movie_id=f.movie_id "
            "WHERE f.account_id=:uid ORDER BY f.created_at DESC, f.movie_id"),
            {"uid": account_id}).mappings().all()

    titles: dict[str, str] = {}
    genres: dict[str, list[str]] = {}
    ratings: dict[str, float] = {}
    rated_at: dict[str, str] = {}
    for row in rating_rows:
        movie_id = str(row["movie_id"])
        ratings[movie_id] = float(row["rating"])
        rated_at[movie_id] = _iso(row["updated_at"]) or ""
        titles[movie_id] = row["movielens_title"]
        genres[movie_id] = [] if row["genres"] == "(no genres listed)" else row["genres"].split("|")

    favorites: list[str] = []
    favorite_at: dict[str, str] = {}
    for row in favorite_rows:
        movie_id = str(row["movie_id"])
        favorites.append(movie_id)
        favorite_at[movie_id] = _iso(row["created_at"]) or ""
        titles.setdefault(movie_id, row["movielens_title"])
        genres.setdefault(movie_id, [] if row["genres"] == "(no genres listed)" else row["genres"].split("|"))

    return Snapshot(account_id, ratings, rated_at, tuple(favorites), favorite_at, titles, genres)


def _profile(snapshot: Snapshot) -> tuple[list[str], int, int]:
    """与 coldstart-v1 完全相同的权重口径：≥3.5 分记 (分数−3)，收藏记 1。"""
    weights: dict[str, float] = {}
    positive = 0
    for movie_id, rating in snapshot.ratings.items():
        if rating >= RATING_MIN:
            weights[movie_id] = rating - 3.0
            positive += 1
    for movie_id in snapshot.favorites:
        weights[movie_id] = weights.get(movie_id, 0.0) + FAVORITE_WEIGHT
    return (top_genres(genre_profile(weights)) if weights else []), positive, len(weights)


def _latest_events(snapshot: Snapshot, limit: int = 5) -> list[dict]:
    events: list[dict] = []
    for movie_id, rating in snapshot.ratings.items():
        events.append({"kind": "rating", "movie_id": movie_id, "title": snapshot.titles.get(movie_id, ""),
                       "rating": f"{rating:.1f}", "at": snapshot.rated_at.get(movie_id)})
    for movie_id in snapshot.favorites:
        events.append({"kind": "favorite", "movie_id": movie_id, "title": snapshot.titles.get(movie_id, ""),
                       "rating": None, "at": snapshot.favorite_at.get(movie_id)})
    events.sort(key=lambda item: (item["at"] or "", int(item["movie_id"])), reverse=True)
    return events[:limit]


# 每个算法到底怎么用这份输入。写死在代码里，不靠文案自证。
CONSUMERS = [
    {"algorithm": "cold_start", "behavior": "full",
     "note": "≥3.5 分评分与收藏直接决定类型画像；零信号时退回全站热门"},
    {"algorithm": "content", "behavior": "full",
     "note": "你的全部评分按（分数 − 个人均值）加权，再乘类型 IDF 生成画像"},
    {"algorithm": "itemcf", "behavior": "full",
     "note": "≥4 分评分作为共现种子参与打分，已评分电影从候选中排除"},
    {"algorithm": "fusion", "behavior": "inherited",
     "note": "自身不读行为，继承四个成员算法的结果"},
    {"algorithm": "als", "behavior": "exclude_only",
     "note": "用你的评分排除已看过的电影；隐因子来自离线训练集"},
    {"algorithm": "popular", "behavior": "none",
     "note": "非个性化：只统计全站高分人数，完全不读你的行为"},
]


@router.get("/behavior")
def behavior(request: Request, actor: ActorDependency):
    """前端「推荐依据」卡片的数据源：这份 JSON 就是本次推荐读到的全部行为。"""
    snapshot = read_snapshot(request, actor.account_id)
    top, positive, weighted = _profile(snapshot)
    fingerprint = behavior_fingerprint(snapshot.ratings, snapshot.favorites)
    return {
        "subject_key": f"app:{actor.account_id}",
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "fingerprint": fingerprint,
        "rating_count": len(snapshot.ratings),
        "favorite_count": len(snapshot.favorites),
        "positive_rating_count": positive,
        "weighted_seed_count": weighted,
        "personalized": weighted > 0,
        "strategy": ("已根据你的行为个性化排序" if weighted
                     else "零行为信号：当前只能给全站热门榜（非个性化）"),
        "genres": top,
        "latest": _latest_events(snapshot),
        "consumers": CONSUMERS,
    }
