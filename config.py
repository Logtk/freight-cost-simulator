import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent

# 現段階(プロトタイプ)ではAI連携なしの単純なStreamlit+SQLite構成のため、
# Gemini関連の設定は持たない。将来AI推奨レート機能等を追加する場合はここに復活させる。
DEFAULT_DB_PATH = BASE_DIR / "data" / "freight_cost.db"


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    db_path: Path
    archive_dir: Path
    log_dir: Path


def load_settings() -> Settings:
    load_dotenv(BASE_DIR / ".env")

    return Settings(
        db_path=Path(os.getenv("DB_PATH", str(DEFAULT_DB_PATH))),
        archive_dir=BASE_DIR / "archive",
        log_dir=BASE_DIR / "logs",
    )


def validate_settings(settings: Settings) -> None:
    # 現段階では必須の外部秘匿情報(APIキー等)がないため検証項目なし。
    # 将来Gemini連携等を追加する場合はここに必須チェックを追加する。
    pass
