from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]
DATASET = "rec-course-v3-train5-1.0.0"
SPLIT = "temporal-v1-cold500-train5-v3"
SCHEMA = "student-eight-v3"
MANIFEST_SHA = "fcfb9a195a56709773696aab02f769c691778af0eb433a1e0778a71c4b289b9e"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APP_", extra="forbid", hide_input_in_errors=True)
    env: Literal["development", "production"] = "production"
    origin: str
    jwt_secret: SecretStr
    jwt_minutes: int = Field(default=30, ge=1, le=60)
    cookie_secure: bool = True
    db_host: str = "127.0.0.1"
    db_port: int = Field(default=3306, ge=1, le=65535)
    db_name: str
    db_user: str
    db_password: SecretStr
    db_socket: str | None = None
    db_ssl_ca: str | None = None
    manifest_path: Path = ROOT / "data/packages" / DATASET / "manifest.json"
    # 实验四 S4：大模型个性化推荐解释。留空则该功能如实返回 503，不伪造解释。
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_model: str = "qwen-plus"
    llm_api_key: SecretStr | None = None
    # 密钥也可以放文件里：课程启动脚本用 --env-file 覆盖了根目录 .env，
    # 走环境变量会丢，所以容器里默认从只读挂载的 /app/llm-secrets/llm-api-key 读。
    llm_key_file: Path | None = None
    llm_timeout: float = Field(default=60.0, ge=5, le=300)

    @model_validator(mode="after")
    def blank_llm_key_is_none(self) -> "Settings":
        if self.llm_api_key is not None and not self.llm_api_key.get_secret_value().strip():
            object.__setattr__(self, "llm_api_key", None)
        if self.llm_api_key is None and self.llm_key_file is not None:
            try:
                text = self.llm_key_file.read_text(encoding="utf-8").strip()
            except OSError:
                text = ""  # 文件不存在等于没配，功能如实返回 503，不影响启动
            if text:
                object.__setattr__(self, "llm_api_key", SecretStr(text))
        return self

    @model_validator(mode="after")
    def secure_settings(self) -> "Settings":
        url = urlsplit(self.origin)
        if url.scheme not in {"http", "https"} or not url.hostname or url.path or url.query or url.fragment or url.username is not None:
            raise ValueError("APP_ORIGIN must be one exact HTTP(S) origin without a trailing slash")
        if len(self.jwt_secret.get_secret_value().encode()) < 32:
            raise ValueError("APP_JWT_SECRET requires at least 32 bytes of independently generated secret")
        if not self.db_user or self.db_user.lower() == "root":
            raise ValueError("The API requires an independent non-root application database user")
        if not self.db_password.get_secret_value():
            raise ValueError("The independent application database password cannot be empty")
        if self.env == "production" and (not self.cookie_secure or url.scheme != "https"):
            raise ValueError("Production requires HTTPS and Secure cookies")
        return self
