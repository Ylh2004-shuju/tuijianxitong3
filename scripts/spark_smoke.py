"""Spark 环境冒烟：确认本地能起 SparkSession 并完成一次 createDataFrame / collect。"""
import os
from pathlib import Path

_VENV_PYTHON = Path(r"C:\Users\Lakers\lab3-venv\Scripts\python.exe")
if _VENV_PYTHON.is_file() and " " not in str(_VENV_PYTHON):
    os.environ.setdefault("PYSPARK_PYTHON", str(_VENV_PYTHON))
    os.environ.setdefault("PYSPARK_DRIVER_PYTHON", str(_VENV_PYTHON))

from pyspark.sql import SparkSession

spark = (SparkSession.builder.master("local[2]").appName("smoke")
         .config("spark.ui.enabled", "false")
         .config("spark.sql.shuffle.partitions", "4")
         .config("spark.driver.memory", "1g").getOrCreate())
df = spark.createDataFrame([(i, i * 2, float(i) + 0.5) for i in range(1000)], ["u", "m", "r"])
print("COUNT:", df.count())
print("COLLECT:", len(df.take(5)))
spark.stop()
print("SMOKE OK")
