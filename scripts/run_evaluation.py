# -*- coding: utf-8 -*-
"""实验三任务2：统一协议离线评价（validation 分区，Precision@10 / Recall@10）。

协议（recommender/evaluation.py 定义）：
  * 固定 4209 个 validation 合法用户，一个都不能少（无结果用户 items=[]，计 0 分）
  * 正相关阈值 4.0；候选 = catalog - 该用户 evaluation/train.csv 已评分电影
  * 四算法的评价模型一律以 evaluation/train.csv 为唯一训练输入
  * 指标 macro 平均：P@10 = 命中数/10；R@10 = 命中数/该用户相关电影数
产出：
  * models/evaluation/<algo>-v1/ 评价模型清单+工件
  * models/evaluation/predictions/<algo>.jsonl 预测文件（经 check_predictions 校验）
  * models/evaluation/results/<algo>.json EvaluationResult
  * models/evaluation/results/summary.json 对比汇总与覆盖统计
"""
import csv
import gzip
import hashlib
import json
import math
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# 项目根目录：默认取脚本所在仓库的上一级，可用 CINELAB_ROOT 覆盖
ROOT = Path(os.environ.get("CINELAB_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
PACKAGE = ROOT / "data" / "packages" / "rec-course-v3-train5-1.0.0"
# 模型输出根目录可用 ALS_OUT_ROOT 覆盖（评价模型/预测/结果都写到其 evaluation/ 子目录）
MODELS = Path(os.environ.get("ALS_OUT_ROOT", str(ROOT / "models")))
EVAL_DIR = MODELS / "evaluation"
DATASET = "rec-course-v3-train5-1.0.0"
SPLIT = "temporal-v1-cold500-train5-v3"
SCHEMA = "student-eight-v3"
MANIFEST_SHA = "fcfb9a195a56709773696aab02f769c691778af0eb433a1e0778a71c4b289b9e"
PARTITION = "validation"
POSITIVE = 4.0
TOP_K = 10
RANK, REG, ITERS, SEED = 16, 0.1, 15, 42

from recommender.data import FrozenData  # noqa: E402
from recommender import evaluation as ev  # noqa: E402
from recommender.evaluation import check_predictions, check_evaluation_result  # noqa: E402


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            value.update(chunk)
    return value.hexdigest()


def dump_gz(path: Path, payload: dict) -> str:
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        json.dump(payload, stream, separators=(",", ":"))
    return sha256(path)


# --------------------------------------------------------------------------- #
# 1. 评价模型构建（全部只用 evaluation/train.csv）
# --------------------------------------------------------------------------- #
def build_models(data: FrozenData, train_rows: list[dict]) -> dict[str, dict]:
    train_sha = data.manifest["files"]["evaluation/train.csv"]["sha256"]
    movies = {}
    for row in data.rows("movies.csv"):
        raw = row["genres"] or ""
        movies[row["movie_id"]] = () if raw in {"", "(no genres listed)"} else tuple(raw.split("|"))
    catalog = data.catalog()

    # --- popular: Bayesian average ---
    sums, counts = defaultdict(float), defaultdict(int)
    for row in train_rows:
        sums[row["movieId"]] += float(row["rating"])
        counts[row["movieId"]] += 1
    prior, strength = sum(sums.values()) / sum(counts.values()), 50
    bayes = {mid: (sums[mid] + prior * strength) / (counts[mid] + strength)
             for mid in counts if counts[mid] >= 100}
    top = sorted(bayes.items(), key=lambda p: (-p[1], int(p[0])))[:500]
    popular_dir = EVAL_DIR / "popular-v1"
    popular_dir.mkdir(parents=True, exist_ok=True)
    artifacts = {"popular_top.json": sha256_with_write(
        popular_dir / "popular_top.json",
        {"model_version": "popular-bayes-eval-v1", "prior": round(prior, 6), "strength": strength,
         "min_rating_count": 100, "top": [[mid, round(score, 6)] for mid, score in top]})}

    # --- content: genre index（静态特征，与业务版一致） ---
    genres = sorted({g for gs in movies.values() for g in gs})
    content_dir = EVAL_DIR / "content-v1"
    content_dir.mkdir(parents=True, exist_ok=True)
    artifacts_c = {"genre_index.json": sha256_with_write(
        content_dir / "genre_index.json",
        {"model_version": "content-genre-eval-v1", "genres": genres})}

    # --- itemcf: cosine co-occurrence over positive ratings ---
    pos_users = defaultdict(set)
    for row in train_rows:
        if float(row["rating"]) >= POSITIVE:
            pos_users[row["movieId"]].add(row["userId"])
    pairs = defaultdict(int)
    # 稀疏共现：按用户聚合其正分电影，两两计数
    user_pos = defaultdict(list)
    for row in train_rows:
        if float(row["rating"]) >= POSITIVE:
            user_pos[row["userId"]].append(row["movieId"])
    for mid_list in user_pos.values():
        mids = sorted(set(mid_list))
        for i, a in enumerate(mids):
            for b in mids[i + 1:]:
                pairs[(a, b)] += 1
    norm = {mid: math.sqrt(len(users)) for mid, users in pos_users.items()}
    neigh = defaultdict(list)
    for (a, b), c in pairs.items():
        if c < 3:
            continue
        sim = c / (norm[a] * norm[b])
        neigh[a].append((b, sim))
        neigh[b].append((a, sim))
    neighbours = {}
    for mid, lst in neigh.items():
        lst.sort(key=lambda p: (-p[1], int(p[0])))
        neighbours[mid] = [[b, round(s, 6)] for b, s in lst[:30]]
    itemcf_dir = EVAL_DIR / "itemcf-v1"
    itemcf_dir.mkdir(parents=True, exist_ok=True)
    with gzip.open(itemcf_dir / "item_neighbors.json.gz", "wt", encoding="utf-8") as stream:
        json.dump({"model_version": "itemcf-cosine-eval-v1", "items": neighbours}, stream, separators=(",", ":"))
    artifacts_i = {"item_neighbors.json.gz": sha256(itemcf_dir / "item_neighbors.json.gz")}

    # --- als: 沿用任务1已训练的评价模型工件 ---
    als_dir = EVAL_DIR / "als-v1"
    als_manifest = json.loads((als_dir / "manifest.json").read_text(encoding="utf-8"))
    als_version = als_manifest["model_version"]

    def write_manifest(directory, algorithm, version, arts, parameters):
        manifest = {
            "algorithm": algorithm, "model_version": version, "runtime_mode": "evaluation",
            "dataset_version": DATASET, "split_version": SPLIT, "schema_version": SCHEMA,
            "manifest_sha256": MANIFEST_SHA,
            "training_files": {"evaluation/train.csv": train_sha},
            "feature_files": {}, "artifacts": arts, "parameters": parameters,
            "tool_versions": {"python": "3.12", "builder": "scripts/run_evaluation.py"},
        }
        (directory / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                                                 encoding="utf-8")
    write_manifest(popular_dir, "popular", "popular-bayes-eval-v1", artifacts,
                   {"ranking": "bayesian_average_of_ratings", "prior": round(prior, 6),
                    "prior_strength": 50, "min_rating_count": 100, "stored_top": 500})
    write_manifest(content_dir, "content", "content-genre-eval-v1", artifacts_c,
                   {"features": "one-hot movie genres", "profile": "sum of (rating-3.0) over rated movies",
                    "scoring": "cosine(profile, movie_genres)", "positive_threshold": POSITIVE})
    write_manifest(itemcf_dir, "itemcf", "itemcf-cosine-eval-v1", artifacts_i,
                   {"similarity": "cosine over binary co-occurrence of ratings>=4.0",
                    "min_cooccurrence": 3, "max_neighbours": 30, "positive_threshold": POSITIVE})
    return {"popular_top": top, "prior": prior, "als_version": als_version}


def sha256_with_write(path: Path, payload: dict) -> str:
    path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    return sha256(path)


# --------------------------------------------------------------------------- #
# 2. 预测生成 + 3. 指标计算
# --------------------------------------------------------------------------- #
def dto(algorithm: str, version: str, items: list[tuple[str, float, list[str]]]) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "algorithm": algorithm, "model_version": version, "dataset_version": DATASET,
        "split_version": SPLIT, "runtime_mode": "evaluation", "status": "ready",
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "items": [{"movie_id": mid, "rank": i + 1, "algorithm": algorithm, "model_version": version,
                   "score": score, "recall_sources": sources}
                  for i, (mid, score, sources) in enumerate(items)],
    }


def main() -> None:
    data = FrozenData(PACKAGE)
    train_rows = list(data.training())
    built = build_models(data, train_rows)

    users = ev.eligible_users(data, PARTITION)
    task_info = ev.task(data, PARTITION)
    catalog = data.catalog()
    print(f"users={len(users)} candidates=catalog-minus-train")

    # 用户排除集：catalog - 该用户 train 已评分
    rated = defaultdict(set)
    for row in train_rows:
        uid = row["userId"]
        if uid in users:
            rated[uid].add(row["movieId"])

    # 用户正分 seeds 与评分画像
    seeds = defaultdict(list)
    for row in train_rows:
        uid = row["userId"]
        if uid in users and float(row["rating"]) >= POSITIVE:
            seeds[uid].append((row["movieId"], float(row["rating"]) - 3.0))

    # genre 表（content 用）
    movies = {}
    for row in data.rows("movies.csv"):
        raw = row["genres"] or ""
        movies[row["movie_id"]] = () if raw in {"", "(no genres listed)"} else tuple(raw.split("|"))
    genre_pos = {g: i for i, g in enumerate(sorted({x for gs in movies.values() for x in gs}))}

    # itemcf 邻居
    with gzip.open(EVAL_DIR / "itemcf-v1" / "item_neighbors.json.gz", "rt", encoding="utf-8") as stream:
        neighbours = json.load(stream)["items"]

    # als 因子（评价模型）
    with gzip.open(EVAL_DIR / "als-v1" / "user_factors.json.gz", "rt", encoding="utf-8") as stream:
        user_factors = json.load(stream)["users"]
    with gzip.open(EVAL_DIR / "als-v1" / "item_factors.json.gz", "rt", encoding="utf-8") as stream:
        item_factors = json.load(stream)["items"]

    popular_top = built["popular_top"]
    versions = {"popular": "popular-bayes-eval-v1", "content": "content-genre-eval-v1",
                "itemcf": "itemcf-cosine-eval-v1", "als": built["als_version"]}

    def recommend(algorithm: str, uid: str) -> list[tuple[str, float, list[str]]]:
        exclude = rated.get(uid, set())
        free = [mid for mid in catalog if mid not in exclude]
        if algorithm == "popular":
            out = [(mid, score, ["全站贝叶斯热门榜"]) for mid, score in popular_top
                   if mid not in exclude][:TOP_K]
            return out
        if algorithm == "content":
            if not seeds.get(uid):
                return []
            profile = [0.0] * len(genre_pos)
            for mid, weight in seeds[uid]:
                for g in movies.get(mid, ()):
                    profile[genre_pos[g]] += weight
            norm = math.sqrt(sum(v * v for v in profile))
            scores = {}
            for mid in free:
                gs = movies.get(mid, ())
                if not gs or norm == 0.0:
                    continue
                dot = sum(profile[genre_pos[g]] for g in gs if g in genre_pos)
                if dot > 0:
                    scores[mid] = round(dot / (norm * math.sqrt(len(gs))), 6)
            ranked = sorted(scores.items(), key=lambda p: (-p[1], int(p[0])))[:TOP_K]
            return [(mid, s, ["类型画像匹配"]) for mid, s in ranked]
        if algorithm == "itemcf":
            if not seeds.get(uid):
                return []
            scores = defaultdict(float)
            evidence = defaultdict(dict)
            for seed_id, weight in seeds[uid]:
                for cand, sim in neighbours.get(seed_id, []):
                    if cand in exclude:
                        continue
                    contribution = sim * weight
                    scores[cand] += contribution
                    evidence[cand][seed_id] = evidence[cand].get(seed_id, 0.0) + contribution
            ranked = sorted(((m, round(s, 6)) for m, s in scores.items()),
                            key=lambda p: (-p[1], int(p[0])))[:TOP_K]
            return [(mid, s, [f"因你看过《电影{seed_id}》"]) for mid, s in ranked]
        if algorithm == "als":
            vec = user_factors.get(uid)
            if not vec:
                return []
            scores = {}
            for mid in free:
                ivec = item_factors.get(mid)
                if not ivec:
                    continue
                scores[mid] = round(sum(a * b for a, b in zip(vec, ivec)), 6)
            ranked = sorted(scores.items(), key=lambda p: (-p[1], int(p[0])))[:TOP_K]
            return [(mid, s, ["LFM 隐向量内积"]) for mid, s in ranked]
        raise ValueError(algorithm)

    # validation 相关集
    relevant = defaultdict(set)
    with (PACKAGE / "evaluation" / "validation.csv").open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            uid = row["userId"]
            if uid in users and float(row["rating"]) >= POSITIVE:
                relevant[uid].add(row["movieId"])

    pred_dir = EVAL_DIR / "predictions"
    pred_dir.mkdir(parents=True, exist_ok=True)
    result_dir = EVAL_DIR / "results"
    result_dir.mkdir(parents=True, exist_ok=True)
    summary = {}
    for algorithm in ["popular", "content", "itemcf", "als"]:
        version = versions[algorithm]
        lines = []
        hits_total = prec_total = rec_total = 0
        non_empty = 0
        covered_movies = set()
        for uid in sorted(users, key=int):
            items = recommend(algorithm, uid)
            if items:
                non_empty += 1
                covered_movies.update(mid for mid, _, _ in items)
            body = dto(algorithm, version, items)
            lines.append(json.dumps({"ml_user_id": uid, "result": body}, ensure_ascii=False))
            hit = len({mid for mid, _, _ in items} & relevant.get(uid, set()))
            hits_total += hit
            prec_total += hit / TOP_K
            if relevant.get(uid):
                rec_total += hit / len(relevant[uid])
        path = pred_dir / f"{algorithm}.jsonl"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        # 框架校验（结构/候选/覆盖）
        check_predictions(path, EVAL_DIR / f"{algorithm}-v1" / "manifest.json", PARTITION, data)
        p10 = prec_total / len(users)
        r10 = rec_total / len(users)
        model_manifest = json.loads((EVAL_DIR / f"{algorithm}-v1" / "manifest.json").read_text(encoding="utf-8"))
        result = {
            "status": "completed", "runtime_mode": "evaluation", "algorithm": algorithm,
            "model_version": version, "task_sha256": task_info["task_sha256"],
            "model_manifest_sha256": sha256(EVAL_DIR / f"{algorithm}-v1" / "manifest.json"),
            "dataset_version": DATASET, "split_version": SPLIT, "schema_version": SCHEMA,
            "partition": PARTITION, "user_count": len(users),
            "precision_at_10": round(p10, 6), "recall_at_10": round(r10, 6),
        }
        (result_dir / f"{algorithm}.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        check_evaluation_result(result_dir / f"{algorithm}.json", EVAL_DIR / f"{algorithm}-v1" / "manifest.json", data)
        summary[algorithm] = {
            **result, "total_hits": hits_total, "users_with_items": non_empty,
            "users_empty": len(users) - non_empty, "coverage_movies": len(covered_movies),
        }
        print(f"{algorithm:9s} P@10={p10:.4f} R@10={r10:.4f} hits={hits_total} "
              f"users_with_items={non_empty} coverage={len(covered_movies)}", flush=True)

    (result_dir / "summary.json").write_text(json.dumps({
        "dataset_version": DATASET, "split_version": SPLIT, "partition": PARTITION,
        "user_count": len(users), "positive_threshold": POSITIVE, "top_k": TOP_K,
        "algorithms": summary}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("ALL DONE")


if __name__ == "__main__":
    main()
