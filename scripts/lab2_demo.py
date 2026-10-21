"""实验二 证据脚本：同一历史用户的内容推荐 / ItemCF 对比 + 冷启动三态。

用途：在没有 Docker/MySQL 的机器上，用业务评分快照复算出与 API 完全一致的
推荐结果，并把可核对的数字写进 reports/lab2-evidence.json。

    python scripts/lab2_demo.py --user 14

脚本只读冻结数据包，不写数据库；它把 Provider 的 ``read_history`` 替换成
"从快照读取"的等价实现，其余路径（模型清单校验、DTO 校验、排序、去重）仍然
走 ``recommender.contracts.recommend`` 的真实代码。
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from recommender.business import Feedback, UserHistory  # noqa: E402
from recommender.contracts import Algorithm, RecommendationRequest, recommend  # noqa: E402
from recommender.data import DATASET, SPLIT  # noqa: E402
from recommender.student import cold_start, content_recommender, itemcf, popular  # noqa: E402
from recommender.student.common import (catalog, movie_genres, movie_table,  # noqa: E402
                                        read_artifact, read_artifact_gz)

PACKAGE = ROOT / "data" / "packages" / "rec-course-v3-train5-1.0.0"
OUTPUT = ROOT / "lab2"


def load_histories() -> dict[tuple[str, int], tuple[list[tuple[str, float]], list[str]]]:
    histories: dict[tuple[str, int], tuple[list[tuple[str, float]], list[str]]] = {}
    path = PACKAGE / "business" / "history_ratings.csv"
    with path.open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            key = ("ml", int(row["userId"]))
            histories.setdefault(key, ([], []))[0].append((row["movieId"], float(row["rating"])))
    return histories


def install_snapshot_history(histories) -> None:
    def fake_read_history(request, database=None):
        ratings, favourites = histories.get((request.subject_kind, request.subject_id), ([], []))
        return UserHistory(
            f"{request.subject_kind}:{request.subject_id}",
            tuple(Feedback(movie_id, f"{rating:.1f}") for movie_id, rating in ratings),
            tuple(favourites),
        )

    for module in (content_recommender, popular, itemcf, cold_start):
        module.read_history = fake_read_history


def run(algorithm: Algorithm, kind: str, uid: int, limit: int = 10):
    return recommend(algorithm, RecommendationRequest(kind, uid, limit, "business", DATASET, SPLIT))


def describe(result) -> list[dict]:
    rows = []
    for item in result.items:
        rows.append({
            "rank": item.rank,
            "movie_id": item.movie_id,
            "title": movie_table()[item.movie_id]["title"],
            "genres": list(movie_genres(item.movie_id)),
            "score": item.score,
            "reasons": item.recall_sources or [],
        })
    return rows


def explain_itemcf_case(uid: int, candidate_id: str, histories, top_seeds: int = 3) -> dict:
    """为报告复算：该候选的分数由哪些正反馈种子贡献。"""
    artifact = read_artifact_gz(ROOT / "models" / "business" / "itemcf-v1" / "item_neighbors.json.gz")
    neighbours = artifact["items"].get(candidate_id, [])
    seed_ratings = {movie_id: rating for movie_id, rating in histories[("ml", uid)][0] if rating >= 4.0}
    contributions = []
    for neighbour_id, similarity in neighbours:
        if neighbour_id in seed_ratings:
            contributions.append({
                "seed_movie_id": neighbour_id,
                "seed_title": movie_table()[neighbour_id]["title"],
                "seed_rating": seed_ratings[neighbour_id],
                "similarity": similarity,
                "contribution": round(similarity * (seed_ratings[neighbour_id] - 3.0), 6),
            })
    contributions.sort(key=lambda row: -row["contribution"])
    return {
        "candidate_id": candidate_id,
        "candidate_title": movie_table()[candidate_id]["title"],
        "candidate_genres": list(movie_genres(candidate_id)),
        "contributing_seeds": contributions[:top_seeds],
        "total_contributing_seeds": len(contributions),
    }


def cold_start_probe(label: str, uid: int, limit: int = 5) -> dict:
    result = run(Algorithm.cold_start, "app", uid, limit)
    rows = describe(result)
    non_personalized = bool(rows) and any("非个性化" in reason or "兜底" in reason
                                         for reason in rows[0]["reasons"])
    return {
        "scenario": label,
        "subject": f"app:{uid}",
        "strategy": "非个性化热门榜" if non_personalized else "偏好推荐（可选信号）",
        "model_version": result.model_version,
        "items": rows,
        "movie_ids": [row["movie_id"] for row in rows],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", type=int, default=14, help="用于对比的 MovieLens 历史用户")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()

    histories = load_histories()
    install_snapshot_history(histories)

    # 追加三个"新账号"场景：零行为 / 一个收藏 / 少量评分
    histories[("app", 900001)] = ([], [])
    histories[("app", 900002)] = ([], ["1"])                      # 收藏 Toy Story
    histories[("app", 900003)] = ([("1", 5.0), ("3114", 4.5), ("480", 4.0)], [])  # Toy Story 1/2 + Jurassic Park

    if ("ml", args.user) not in histories:
        raise SystemExit(f"历史用户 {args.user} 不存在")

    content_result = run(Algorithm.content, "ml", args.user, args.limit)
    itemcf_result = run(Algorithm.itemcf, "ml", args.user, args.limit)
    popular_result = run(Algorithm.popular, "ml", args.user, args.limit)
    content_rows, itemcf_rows = describe(content_result), describe(itemcf_result)

    rated = {movie_id for movie_id, _ in histories[("ml", args.user)][0]}
    itemcf_ids = [row["movie_id"] for row in itemcf_rows]
    content_ids = [row["movie_id"] for row in content_rows]
    overlap = sorted(set(itemcf_ids) & set(content_ids), key=int)

    case = explain_itemcf_case(args.user, itemcf_ids[0], histories) if itemcf_ids else None

    probes = [
        cold_start_probe("新账号 · 零评分零收藏", 900001),
        cold_start_probe("新账号 · 只收藏 Toy Story", 900002),
        cold_start_probe("新账号 · 3 条评分（Toy Story 1/2 + Jurassic Park）", 900003),
    ]
    zero, one_favorite, three_ratings = probes
    changed = sorted(set(one_favorite["movie_ids"]) - set(zero["movie_ids"]), key=int)

    evidence = {
        "subject": f"ml:{args.user}",
        "history_size": len(histories[("ml", args.user)][0]),
        "positive_seeds": sum(1 for _, rating in histories[("ml", args.user)][0] if rating >= 4.0),
        "popular_head": [{"rank": row["rank"], "movie_id": row["movie_id"], "title": row["title"],
                          "score": row["score"], "reason": row["reasons"][0]} for row in describe(popular_result)],
        "content": content_rows,
        "itemcf": itemcf_rows,
        "overlap_at_10": len(overlap),
        "overlap_ids": overlap,
        "itemcf_duplicate": len(itemcf_ids) != len(set(itemcf_ids)),
        "itemcf_seen_leak": sorted(rated & set(itemcf_ids), key=int),
        "itemcf_all_in_catalog": all(movie_id in catalog() for movie_id in itemcf_ids),
        "case": case,
        "cold_start": probes,
        "cold_start_changed_after_favorite": changed,
        "artifact_stats": {
            "catalog": len(catalog()),
            "itemcf_items_with_neighbours": len(read_artifact_gz(
                ROOT / "models/business/itemcf-v1/item_neighbors.json.gz")["items"]),
            "popular_pool": len(read_artifact(
                ROOT / "models/business/popular-v1/popular_top.json")["items"]),
        },
    }
    OUTPUT.mkdir(exist_ok=True)
    (OUTPUT / "lab2-evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def table(title: str, rows: list[dict]) -> None:
        print(f"\n== {title} ==")
        for row in rows:
            reason = row["reasons"][0] if row["reasons"] else ""
            print(f"{row['rank']:>2}. {row['movie_id']:>6}  {row['title'][:46]:<48} "
                  f"score={row['score']:.4f}  {'/'.join(row['genres'])[:28]:<30} {reason}")

    print(f"subject ml:{args.user}  历史评分 {evidence['history_size']} 条，正反馈 {evidence['positive_seeds']} 条")
    table("content · 内容推荐", content_rows)
    table("itemcf · 物品协同过滤", itemcf_rows)
    print(f"\nTop-10 重叠 {len(overlap)} 部：{overlap}")
    print(f"ItemCF 重复: {evidence['itemcf_duplicate']}  已评分泄漏: {evidence['itemcf_seen_leak']}  "
          f"目录外: {not evidence['itemcf_all_in_catalog']}")
    if case:
        print(f"\n案例：{case['candidate_title']} ({'/'.join(case['candidate_genres'])})")
        for row in case["contributing_seeds"]:
            print(f"   ← 《{row['seed_title']}》 评分 {row['seed_rating']}  相似度 {row['similarity']:.4f}  "
                  f"贡献 {row['contribution']:.4f}")
    for probe in probes:
        print(f"\n== 冷启动：{probe['scenario']} -> {probe['strategy']} ==")
        for row in probe["items"]:
            print(f"  {row['rank']}. {row['title'][:46]:<48} {row['reasons'][0] if row['reasons'] else ''}")
    print(f"\n收藏后结果变化的新电影：{changed}")
    print(f"\n证据写入 {OUTPUT / 'lab2-evidence.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
