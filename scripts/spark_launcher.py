# -*- coding: utf-8 -*-
"""Windows 本地 Spark 网关启动辅助（不依赖 spark-submit.cmd）。

PySpark 在 Windows 上通过 ``bin\\spark-submit.cmd`` 启动 JVM，
该脚本内部需要 ``cmd``。在禁止调用 ``cmd`` 的受限终端里会报
``JAVA_GATEWAY_EXITED``。本模块直接以 ``java`` 启动同一个入口类
``org.apache.spark.deploy.SparkSubmit``，并把连接信息回传给 PySpark，
效果与官方脚本一致：

    os.environ["SPARK_MANUAL_GATEWAY"] = "1"   # 启用本方式
    import spark_launcher; spark_launcher.ensure_gateway()
    SparkSession.builder...getOrCreate()       # 不再自行启动 JVM

未设置 SPARK_MANUAL_GATEWAY 时本模块什么都不做，行为与官方一致。
"""
import atexit
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_JVM = None


def _java_executable() -> str:
    for candidate in (os.environ.get("JAVA_HOME"), None):
        if candidate:
            exe = Path(candidate) / "bin" / "java"
            if exe.is_file():
                return str(exe)
    found = shutil.which("java")
    if found:
        return found
    raise RuntimeError("未找到 java：请设置 JAVA_HOME 或把 java 放进 PATH")


def ensure_gateway(master: str = "local[2]", driver_memory: str = "2g",
                   shuffle_partitions: int = 8) -> None:
    """以独立进程启动 Spark JVM 网关，并让 PySpark 直接连接它。"""
    global _JVM
    if os.environ.get("SPARK_MANUAL_GATEWAY") != "1" or _JVM is not None:
        return

    import pyspark  # noqa: F401  (import 只为定位 SPARK_HOME)

    spark_home = Path(pyspark.__file__).resolve().parent
    conn_dir = Path(tempfile.mkdtemp(prefix="spark-conn-"))
    conn_file = conn_dir / "conn.info"

    env = dict(os.environ)
    env["SPARK_HOME"] = str(spark_home)
    env["_PYSPARK_DRIVER_CONN_INFO_PATH"] = str(conn_file)
    env.setdefault("PYSPARK_PYTHON", sys.executable)
    env.setdefault("PYSPARK_DRIVER_PYTHON", sys.executable)

    command = [
        _java_executable(),
        f"-Xmx{driver_memory}",
        "-cp", str(spark_home / "jars" / "*"),
        "org.apache.spark.deploy.SparkSubmit",
        "--master", master,
        "--conf", "spark.ui.enabled=false",
        "--conf", f"spark.driver.memory={driver_memory}",
        "--conf", f"spark.sql.shuffle.partitions={shuffle_partitions}",
        "pyspark-shell",
    ]
    log_path = conn_dir / "gateway.log"
    with log_path.open("wb") as log:
        _JVM = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=log, stderr=log, env=env)

    deadline = time.time() + 120
    while time.time() < deadline:
        if _JVM.poll() is not None:
            raise RuntimeError(f"Spark 网关进程退出；日志：{log_path}")
        if conn_file.is_file() and conn_file.stat().st_size > 8:
            break
        time.sleep(0.2)
    else:
        raise RuntimeError("等待 Spark 网关连接信息超时")

    from pyspark.java_gateway import read_int
    from pyspark.serializers import UTF8Deserializer

    with conn_file.open("rb") as stream:
        port = read_int(stream)
        secret = UTF8Deserializer().loads(stream)

    os.environ["PYSPARK_GATEWAY_PORT"] = str(port)
    os.environ["PYSPARK_GATEWAY_SECRET"] = secret

    def _shutdown() -> None:
        try:
            _JVM.kill()
        except Exception:  # noqa: BLE001 - 进程可能已经退出
            pass

    atexit.register(_shutdown)
