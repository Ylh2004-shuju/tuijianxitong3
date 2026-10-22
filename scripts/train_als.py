# -*- coding: utf-8 -*-
"""实验三任务1 + 改进：使用冻结的 train 完成 Spark MLlib ALS 训练。

产出两套真实模型（同一脚本、同一 Spark 参数，仅训练输入不同）：
  * models/business/als-v1/   runtime_mode=business    训练输入 business/history_ratings.csv
  * models/evaluation/als-v1/ runtime_mode=evaluation  训练输入 evaluation/train.csv

模型形态（v2）：
    r̂(u, i) = μ + b_u + b_i + p_u · q_i
Spark MLlib ALS 的超参数保持课程规范要求（rank=16 / regParam=0.1 / maxIter=15 /
seed=42），但拟合目标改为**去基线后的残差**：

    residual(u, i) = r(u, i) − μ − b_u − b_i

这样隐向量只负责"个体差异"，热门与评分尺度由 μ / b_i 承担，长尾电影不再靠少数
几条评分把内积推到 5 分制以外。基线带收缩（默认 10 条），避免少量评分被过拟合。

残差有正有负，因此不再使用 nonnegative 约束。

每套模型包含：user_factors.json.gz / item_factors.json.gz / biases.json.gz + 清单。
"""
import csv
import gzip
import hashlib
import json
import os
import sys
from pathlib import Path

# 项目根目录：默认取脚本所在仓库的上一级，可用 CINELAB_ROOT 覆盖
ROOT = Path(os.environ.get("CINELAB_ROOT", Path(__file__).resolve().parents[1]))
PACKAGE = ROOT / "data" / "packages" / "rec-course-v3-train5-1.0.0"
# 输出根目录可用 ALS_OUT_ROOT 覆盖（默认直接写 models/）
MODELS = Path(os.environ.get("ALS_OUT_ROOT", str(ROOT / "models")))
# Windows 本地训练：venv Python 必须是无空格路径，否则 Spark Python worker 起不来。
# 只有该解释器真实存在时才注入，换机器时自动退回当前解释器（可用环境变量显式指定）。
_VENV_PYTHON = Path(r"C:\Users\Lakers\lab3-venv\Scripts\python.exe")
if _VENV_PYTHON.is_file() and " " not in str(_VENV_PYTHON):
    os.environ.setdefault("PYSPARK_PYTHON", str(_VENV_PYTHON))
    os.environ.setdefault("PYSPARK_DRIVER_PYTHON", str(_VENV_PYTHON))

DATASET = "rec-course-v3-train5-1.0.0"
SPLIT = "temporal-v1-cold500-train5-v3"
SCHEMA = "student-eight-v3"
MANIFEST_SHA = "fcfb9a195a56709773696aab02f769c691778af0eb433a1e0778a71c4b289b9e"

# Spark MLlib ALS 超参数：保持课程规范要求不变
RANK, REG, ITERS, SEED = 16, 0.1, 15, 42
# 课程规范要求以上超参数保持不变；做参数扫描时用环境变量临时覆盖，默认值仍为规范值
RANK = int(os.environ.get("ALS_RANK", RANK))
REG = float(os.environ.get("ALS_REG", REG))
ITERS = int(os.environ.get("ALS_ITERS", ITERS))
# 改进项：基线收缩强度、候选曝光过滤阈值
BIAS_SHRINK = 10
MIN_EXPOSURE = 20
MODEL_VERSION = "als-baseline-residual-v2"


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


def train(spark, train_csv: Path):
    """返回 (user_factors, item_factors, biases)。全部计算留在 JVM 侧。"""
    from pyspark.ml.recommendation import ALS
    from pyspark.sql import functions as F

    # 直接由 JVM 读取冻结 CSV，绕开 Python worker 数据转换
    df = (spark.read.option("header", True)
          .schema("userId INT, movieId INT, rating DOUBLE")
          .csv(train_csv.as_posix())).repartition(8)

    mu = float(df.agg(F.avg("rating")).first()[0])

    # 电影基线：b_i = Σ(r − μ) / (n_i + k)，同时得到曝光度 n_i
    item = df.groupBy("movieId").agg(F.count("rating").alias("n"), F.sum("rating").alias("s"))
    item_bias = item.withColumn("bi", (F.col("s") - F.lit(mu) * F.col("n")) / (F.col("n") + F.lit(BIAS_SHRINK)))
    # 用户基线：b_u = Σ(r − μ − b_i) / (n_u + k)
    with_item = df.join(item_bias.select("movieId", "bi"), "movieId")
    residue = with_item.withColumn("r2", F.col("rating") - F.lit(mu) - F.col("bi"))
    user = residue.groupBy("userId").agg(F.count("rating").alias("n"), F.sum("r2").alias("s"))
    user_bias = user.withColumn("bu", F.col("s") / (F.col("n") + F.lit(BIAS_SHRINK)))

    residual = (with_item.join(user_bias.select("userId", "bu"), "userId")
                .withColumn("residual", F.col("rating") - F.lit(mu) - F.col("bi") - F.col("bu"))
                .select("userId", "movieId", "residual"))

    als = ALS(userCol="userId", itemCol="movieId", ratingCol="residual", rank=RANK,
              regParam=REG, maxIter=ITERS, seed=SEED, coldStartStrategy="drop",
              nonnegative=False)
    model = als.fit(residual)

    user_factors = {str(int(r["id"])): [round(float(v), 6) for v in r["features"]]
                    for r in model.userFactors.select("id", "features").collect()}
    item_factors = {str(int(r["id"])): [round(float(v), 6) for v in r["features"]]
                    for r in model.itemFactors.select("id", "features").collect()}
    biases = {
        "mu": round(mu, 6), "shrink": BIAS_SHRINK, "min_exposure": MIN_EXPOSURE,
        "users": {str(int(r["userId"])): round(float(r["bu"]), 6)
                  for r in user_bias.select("userId", "bu").collect()},
        "items": {str(int(r["movieId"])): round(float(r["bi"]), 6)
                  for r in item_bias.select("movieId", "bi").collect()},
        "item_counts": {str(int(r["movieId"])): int(r["n"])
                        for r in item_bias.select("movieId", "n").collect()},
    }
    return user_factors, item_factors, biases


def build(train_csv: Path, out_dir: Path, runtime_mode: str, training_key: str,
          package_manifest: dict) -> None:
    from pyspark.sql import SparkSession

    out_dir.mkdir(parents=True, exist_ok=True)
    n_rows = 0
    with train_csv.open(encoding="utf-8", newline="") as stream:
        for _ in csv.DictReader(stream):
            n_rows += 1
    print(f"[{runtime_mode}] rows={n_rows} train_csv={train_csv}", flush=True)

    spark = (SparkSession.builder.master("local[4]").appName(f"cinelab-als-{runtime_mode}")
             .config("spark.ui.enabled", "false")
             .config("spark.sql.shuffle.partitions", "8")
             .config("spark.driver.memory", "2g").getOrCreate())
    try:
        user_factors, item_factors, biases = train(spark, train_csv)
    finally:
        spark.stop()

    artifacts = {
        "user_factors.json.gz": dump_gz(out_dir / "user_factors.json.gz", {"users": user_factors}),
        "item_factors.json.gz": dump_gz(out_dir / "item_factors.json.gz", {"items": item_factors}),
        "biases.json.gz": dump_gz(out_dir / "biases.json.gz", biases),
    }
    manifest = {
        "algorithm": "als",
        "model_version": MODEL_VERSION,
        "runtime_mode": runtime_mode,
        "dataset_version": DATASET,
        "split_version": SPLIT,
        "schema_version": SCHEMA,
        "manifest_sha256": MANIFEST_SHA,
        "training_files": {training_key: package_manifest["files"][training_key]["sha256"]},
        "feature_files": {},
        "artifacts": artifacts,
        "parameters": {
            # Spark MLlib ALS 超参数保持规范要求
            "model": "Spark MLlib ALS (LFM latent factors)",
            "rank": RANK, "regParam": REG, "maxIter": ITERS, "seed": SEED,
            "coldStartStrategy": "drop", "nonnegative": False,
            # 改进项
            "target": "residual = rating - mu - b_u - b_i",
            "bias_shrinkage": BIAS_SHRINK,
            "prediction": "mu + b_u + b_i + dot(user_factors, item_factors)",
            "candidate_rule": f"movie train rating count >= {MIN_EXPOSURE}",
            "min_exposure": MIN_EXPOSURE,
            "training_rows": n_rows,
        },
        "tool_versions": {"python": "3.12", "pyspark": "3.5.1",
                          "builder": "scripts/train_als.py"},
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                                           encoding="utf-8")
    print(f"[{runtime_mode}] done users={len(user_factors)} items={len(item_factors)} "
          f"mu={biases['mu']} -> {out_dir}", flush=True)


def main() -> None:
    package_manifest = json.loads((PACKAGE / "manifest.json").read_text(encoding="utf-8"))
    if sha256(PACKAGE / "manifest.json") != MANIFEST_SHA:
        raise SystemExit("数据包 manifest 摘要不符")

    # 受限终端里 spark-submit.cmd 无法使用时的替代网关（详见 spark_launcher.py）
    if os.environ.get("SPARK_MANUAL_GATEWAY") == "1":
        sys.path.insert(0, str(ROOT / "scripts"))
        import spark_launcher
        spark_launcher.ensure_gateway(master="local[4]", driver_memory="2g", shuffle_partitions=8)

    jobs = [
        (PACKAGE / "business" / "history_ratings.csv", MODELS / "business" / "als-v1",
         "business", "business/history_ratings.csv"),
        (PACKAGE / "evaluation" / "train.csv", MODELS / "evaluation" / "als-v1",
         "evaluation", "evaluation/train.csv"),
    ]
    # 扫参时只训 evaluation 一份（ALS_ONLY=evaluation），省一半时间
    only = os.environ.get("ALS_ONLY")
    if only:
        jobs = [job for job in jobs if job[2] == only]
    print(f"regParam={REG} rank={RANK} maxIter={ITERS} jobs={[job[2] for job in jobs]}", flush=True)
    for train_csv, out_dir, runtime_mode, training_key in jobs:
        build(train_csv, out_dir, runtime_mode, training_key, package_manifest)
    print("ALL DONE")


if __name__ == "__main__":
    sys.exit(main())
