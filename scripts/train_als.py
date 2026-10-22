# -*- coding: utf-8 -*-
"""实验三任务1：使用冻结的 train 完成 Spark MLlib ALS 显式评分训练。

产出两套真实模型（同一脚本、同一参数，仅训练输入不同）：
  * models/business/als-v1/   runtime_mode=business    训练输入 business/history_ratings.csv
  * models/evaluation/als-v1/ runtime_mode=evaluation  训练输入 evaluation/train.csv
每套包含 user_factors.json.gz / item_factors.json.gz 工件与 ModelManifest 清单。
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

RANK, REG, ITERS, SEED = 16, 0.1, 15, 42
MODEL_VERSION = "als-implicit-explicit-v1"


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


def main() -> None:
    jobs = [
        (PACKAGE / "business" / "history_ratings.csv", MODELS / "business" / "als-v1",
         "business", "business/history_ratings.csv"),
        (PACKAGE / "evaluation" / "train.csv", MODELS / "evaluation" / "als-v1",
         "evaluation", "evaluation/train.csv"),
    ]
    package_manifest = json.loads((PACKAGE / "manifest.json").read_text(encoding="utf-8"))
    data_manifest_sha = sha256(PACKAGE / "manifest.json")
    assert data_manifest_sha == MANIFEST_SHA, "数据包 manifest 摘要不符"

    for train_csv, out_dir, runtime_mode, training_key in jobs:
        from pyspark.sql import SparkSession
        from pyspark.ml.recommendation import ALS

        out_dir.mkdir(parents=True, exist_ok=True)
        # 行数统计（纯 Python，不进 Spark）
        n_rows = 0
        with train_csv.open(encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            for _ in reader:
                n_rows += 1
        print(f"[{runtime_mode}] rows={n_rows} train_csv={train_csv}", flush=True)

        spark = (SparkSession.builder.master("local[4]").appName(f"cinelab-als-{runtime_mode}")
                 .config("spark.ui.enabled", "false")
                 .config("spark.sql.shuffle.partitions", "8")
                 .config("spark.driver.memory", "2g").getOrCreate())
        try:
            # 直接由 JVM 读取冻结 CSV，绕开 Python worker 数据转换
            df = (spark.read.option("header", True)
                  .schema("userId INT, movieId INT, rating DOUBLE")
                  .csv(train_csv.as_posix()))
            als = ALS(userCol="userId", itemCol="movieId", ratingCol="rating", rank=RANK,
                      regParam=REG, maxIter=ITERS, seed=SEED, coldStartStrategy="drop",
                      nonnegative=True)
            model = als.fit(df)
            user_factors = {str(int(r["id"])): [round(float(v), 6) for v in r["features"]]
                            for r in model.userFactors.select("id", "features").collect()}
            item_factors = {str(int(r["id"])): [round(float(v), 6) for v in r["features"]]
                            for r in model.itemFactors.select("id", "features").collect()}
        finally:
            spark.stop()

        artifacts = {
            "user_factors.json.gz": dump_gz(out_dir / "user_factors.json.gz", {"users": user_factors}),
            "item_factors.json.gz": dump_gz(out_dir / "item_factors.json.gz", {"items": item_factors}),
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
                "model": "Spark MLlib ALS explicit ratings (LFM latent factors)",
                "rank": RANK, "regParam": REG, "maxIter": ITERS, "seed": SEED,
                "coldStartStrategy": "drop", "nonnegative": True,
                "prediction": "dot(user_factors, item_factors)",
                "training_rows": n_rows,
            },
            "tool_versions": {"python": "3.12", "pyspark": "3.5.1",
                              "builder": "scripts/train_als.py"},
        }
        (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                                               encoding="utf-8")
        print(f"[{runtime_mode}] done users={len(user_factors)} items={len(item_factors)} -> {out_dir}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
