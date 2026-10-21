"""构建业务模式模型工件（热门榜 / ItemCF / 冷启动）。

在宿主机独立运行，绝不在 HTTP 进程内训练：

    python scripts/build_business_models.py

只读取封存的业务评分快照 ``business/history_ratings.csv``（业务 API 使用的同一份
数据），按 ``manifest.json`` 校验 SHA-256 后写出：

    models/business/popular-v1/popular_top.json      + manifest.json
    models/business/itemcf-v1/item_neighbors.json.gz + manifest.json
    models/business/coldstart-v1/popular_top.json    + strategy.json + manifest.json

Provider 只读取这些工件，导入时和请求时都不训练。
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import scipy
from scipy import sparse

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "data" / "packages" / "rec-course-v3-train5-1.0.0"
MODELS = ROOT / "models" / "business"
MANIFEST_SHA = "fcfb9a195a56709773696aab02f769c691778af0eb433a1e0778a71c4b289b9e"
DATASET = "rec-course-v3-train5-1.0.0"
SPLIT = "temporal-v1-cold500-train5-v3"
SCHEMA = "student-eight-v3"
TRAINING_FILE = "business/history_ratings.csv"
FEATURE_FILES = ("movies.csv", "training-catalog.csv")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_package() -> dict:
    manifest_path = PACKAGE / "manifest.json"
    if not manifest_path.is_file() or sha256(manifest_path) != MANIFEST_SHA:
        raise SystemExit("manifest.json 摘要不符，拒绝构建")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    identity = tuple(manifest[key] for key in ("dataset_version", "split_version", "schema_version"))
    if identity != (DATASET, SPLIT, SCHEMA):
        raise SystemExit("数据版本身份不符：" + repr(identity))
    path = PACKAGE / TRAINING_FILE
    if sha256(path) != manifest["files"][TRAINING_FILE]["sha256"]:
        raise SystemExit("business 评分快照摘要不符")
    return manifest


def load_movies() -> dict[str, dict]:
    movies: dict[str, dict] = {}
    with (PACKAGE / "movies.csv").open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            raw = row["genres"] or ""
            genres = () if raw in {"", "(no genres listed)"} else tuple(g for g in raw.split("|") if g)
            movies[row["movie_id"]] = {"title": row["movielens_title"], "genres": genres}
    return movies


def load_catalog() -> list[str]:
    with (PACKAGE / "training-catalog.csv").open(encoding="utf-8", newline="") as stream:
        ids = [row["movie_id"] for row in csv.DictReader(stream)]
    if len(ids) != len(set(ids)) or len(ids) != 8459:
        raise SystemExit("训练目录数量或唯一性不符")
    return ids


def load_business(catalog: list[str]) -> dict:
    """Read the sealed business snapshot once and build the implicit matrices."""
    index = {movie_id: position for position, movie_id in enumerate(catalog)}
    users: dict[str, int] = {}
    ratings: list[tuple[int, int, float]] = []
    with (PACKAGE / TRAINING_FILE).open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            movie_id = row["movieId"]
            if movie_id not in index:
                raise SystemExit("业务快照包含目录外电影：" + movie_id)
            user = users.setdefault(row["userId"], len(users))
            ratings.append((user, index[movie_id], float(row["rating"])))
    return {"users": users, "ratings": ratings, "index": index}


def build_popular(catalog: list[str], movies: dict[str, dict], data: dict, min_count: int, prior: int, top: int) -> dict:
    counts = np.zeros(len(catalog), dtype=np.int64)
    sums = np.zeros(len(catalog), dtype=np.float64)
    for _, item, rating in data["ratings"]:
        counts[item] += 1
        sums[item] += rating
    total, total_sum = int(counts.sum()), float(sums.sum())
    global_mean = total_sum / total
    entries = []
    for position, movie_id in enumerate(catalog):
        count = int(counts[position])
        if count < min_count:
            continue
        mean = sums[position] / count
        # Bayesian average: keeps a 5.0 from 3 raters below a 4.4 from 2,000.
        score = (count / (count + prior)) * mean + (prior / (count + prior)) * global_mean
        entries.append({
            "movie_id": movie_id,
            "rating_count": count,
            "mean_rating": round(mean, 3),
            "popularity_score": round(score, 6),
        })
    entries.sort(key=lambda entry: (-entry["popularity_score"], int(entry["movie_id"])))
    entries = entries[:top]
    return {
        "schema": "cinelab-popular-v1",
        "dataset_version": DATASET,
        "split_version": SPLIT,
        "source_file": TRAINING_FILE,
        "global_mean_rating": round(global_mean, 6),
        "min_rating_count": min_count,
        "prior_strength": prior,
        "total_ratings": total,
        "eligible_movies": int(sum(1 for count in counts if count >= min_count)),
        "items": entries,
    }


def build_itemcf(catalog: list[str], data: dict, k: int, min_co: int, positive: float, chunk: int) -> dict:
    n_users, n_items = len(data["users"]), len(catalog)
    rows, cols = [], []
    for user, item, rating in data["ratings"]:
        if rating >= positive:
            rows.append(user)
            cols.append(item)
    matrix = sparse.csr_matrix((np.ones(len(rows), dtype=np.float32), (rows, cols)), shape=(n_users, n_items))
    item_support = np.asarray(matrix.sum(axis=0)).ravel().astype(np.float64)
    items_t = matrix.T.tocsr()
    neighbours: dict[str, list] = {}
    for start in range(0, n_items, chunk):
        stop = min(start + chunk, n_items)
        block = (items_t[start:stop] @ matrix).toarray().astype(np.float64)
        support = item_support[start:stop]
        denominator = np.sqrt(np.maximum(support, 1e-12))[:, None] * np.sqrt(np.maximum(item_support, 1e-12))[None, :]
        similarity = np.divide(block, denominator, out=np.zeros_like(block), where=denominator > 0)
        similarity[block < min_co] = 0.0
        for offset in range(stop - start):
            position = start + offset
            similarity[offset, position] = 0.0
            row = similarity[offset]
            positive_columns = np.flatnonzero(row > 0)
            if positive_columns.size == 0:
                continue
            if positive_columns.size > k:
                candidates = row[positive_columns]
                chosen = np.argpartition(-candidates, k - 1)[:k]
                positive_columns = positive_columns[chosen]
            ordered = sorted(positive_columns.tolist(), key=lambda column: (-round(float(row[column]), 6), int(catalog[column])))
            neighbours[catalog[position]] = [[catalog[column], round(float(row[column]), 6)] for column in ordered]
    return {
        "schema": "cinelab-itemcf-v1",
        "dataset_version": DATASET,
        "split_version": SPLIT,
        "source_file": TRAINING_FILE,
        "positive_rating_threshold": positive,
        "min_cooccurrence": min_co,
        "max_neighbours": k,
        "similarity": "cosine over binary positive feedback",
        "total_items": n_items,
        "items_with_neighbours": len(neighbours),
        "items": neighbours,
    }


def write_manifest(directory: Path, algorithm: str, model_version: str, artifacts: dict[str, str],
                   parameters: dict, package_manifest: dict) -> None:
    manifest = {
        "algorithm": algorithm,
        "model_version": model_version,
        "runtime_mode": "business",
        "dataset_version": DATASET,
        "split_version": SPLIT,
        "schema_version": SCHEMA,
        "manifest_sha256": MANIFEST_SHA,
        "training_files": {TRAINING_FILE: package_manifest["files"][TRAINING_FILE]["sha256"]},
        "feature_files": {name: package_manifest["files"][name]["sha256"] for name in FEATURE_FILES},
        "artifacts": artifacts,
        "parameters": parameters,
        "tool_versions": {
            "python": "3.13",
            "numpy": np.__version__,
            "scipy": scipy.__version__,
            "builder": "scripts/build_business_models.py",
        },
    }
    (directory / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-rating-count", type=int, default=100, help="热门榜最低评分数")
    parser.add_argument("--prior", type=int, default=50, help="贝叶斯平滑先验强度 m")
    parser.add_argument("--top", type=int, default=500, help="热门榜保存条数")
    parser.add_argument("--neighbours", type=int, default=30, help="每部电影保留的相似邻居数")
    parser.add_argument("--min-cooccurrence", type=int, default=3, help="物品共现最低共同用户数")
    parser.add_argument("--positive", type=float, default=4.0, help="正反馈评分阈值")
    parser.add_argument("--chunk", type=int, default=250, help="相似度分块大小")
    args = parser.parse_args()

    package_manifest = verify_package()
    catalog = load_catalog()
    movies = load_movies()
    data = load_business(catalog)
    if len(data["ratings"]) != package_manifest["files"][TRAINING_FILE]["records"]:
        raise SystemExit("业务评分行数与 manifest 不符")
    print(f"snapshot: {len(data['ratings']):,} ratings, {len(data['users']):,} users, {len(catalog):,} catalog movies")

    # ---------------------------------------------------------------- popular
    popular_dir = MODELS / "popular-v1"
    popular_dir.mkdir(parents=True, exist_ok=True)
    popular = build_popular(catalog, movies, data, args.min_rating_count, args.prior, args.top)
    popular_path = popular_dir / "popular_top.json"
    popular_path.write_text(json.dumps(popular, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    write_manifest(popular_dir, "popular", "popular-bayes-v1", {"popular_top.json": sha256(popular_path)}, {
        "ranking": "bayesian_average_of_ratings",
        "prior_strength": args.prior,
        "min_rating_count": args.min_rating_count,
        "stored_top": args.top,
        "personalization": "none",
    }, package_manifest)
    top = popular["items"][0]
    print(f"popular: {len(popular['items'])} items (top {top['movie_id']} {movies[top['movie_id']]['title']}, "
          f"{top['rating_count']} ratings, mean {top['mean_rating']})")

    # ----------------------------------------------------------------- itemcf
    itemcf_dir = MODELS / "itemcf-v1"
    itemcf_dir.mkdir(parents=True, exist_ok=True)
    itemcf = build_itemcf(catalog, data, args.neighbours, args.min_cooccurrence, args.positive, args.chunk)
    itemcf_path = itemcf_dir / "item_neighbors.json.gz"
    with gzip.open(itemcf_path, "wt", encoding="utf-8", compresslevel=6) as stream:
        json.dump(itemcf, stream, ensure_ascii=False, separators=(",", ":"))
    write_manifest(itemcf_dir, "itemcf", "itemcf-cosine-v1", {"item_neighbors.json.gz": sha256(itemcf_path)}, {
        "similarity": "cosine",
        "positive_rating_threshold": args.positive,
        "min_cooccurrence": args.min_cooccurrence,
        "max_neighbours": args.neighbours,
        "neighbour_selection": "top_k_per_item",
        "scoring": "sum(similarity * (rating - 3.0)) over the user's positive seeds",
    }, package_manifest)
    sample = next((movie_id for movie_id, values in itemcf["items"].items() if len(values) >= 10), None)
    print(f"itemcf: {itemcf['items_with_neighbours']:,} items with neighbours, "
          f"file {itemcf_path.stat().st_size / 1e6:.1f} MB"
          + (f"; e.g. {movies[sample]['title']} -> {movies[itemcf['items'][sample][0][0]]['title']}" if sample else ""))

    # -------------------------------------------------------------- coldstart
    cold_dir = MODELS / "coldstart-v1"
    cold_dir.mkdir(parents=True, exist_ok=True)
    cold_popular_path = cold_dir / "popular_top.json"
    cold_popular_path.write_text(
        json.dumps({**popular, "items": popular["items"][:args.top]}, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")
    strategy_path = cold_dir / "strategy.json"
    strategy = {
        "schema": "cinelab-coldstart-v1",
        "description": "无行为时回退到全站热门榜；有评分/收藏时使用类型偏好并叠加小幅热门先验。",
        "signals": {"rating_min": 3.5, "rating_weight": "rating - 3.0", "favorite_weight": 1.0},
        "scoring": {"content_weight": 0.85, "popularity_weight": 0.15},
        "fallback": "popular_top.json",
        "personalization_claim": "only when the account has at least one rating >= 3.5 or one favorite",
    }
    strategy_path.write_text(json.dumps(strategy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_manifest(cold_dir, "cold_start", "coldstart-v1", {
        "popular_top.json": sha256(cold_popular_path),
        "strategy.json": sha256(strategy_path),
    }, {
        "rating_min": 3.5,
        "favorite_weight": 1.0,
        "content_weight": 0.85,
        "popularity_weight": 0.15,
        "max_popular_fallback": args.top,
    }, package_manifest)
    print(f"coldstart: fallback pool {len(popular['items'])} items, strategy thresholds written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
