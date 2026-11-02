from functools import lru_cache
from pathlib import Path
import yaml

from app.core.config import settings


@lru_cache(maxsize=1)
def runtime_config():
    path = Path(settings.CONFIG_YAML_PATH)
    if not path.is_file():
        path = Path(__file__).resolve().parents[3] / "config" / "config.yaml"
    with path.open(encoding="utf-8") as handle:
        document = yaml.safe_load(handle) or {}
    for section in ("service", "storage", "networks"):
        if section not in document:
            raise RuntimeError(f"config.yaml에 {section} 섹션이 필요합니다.")
    return document

def storage_path(name: str) -> Path:
    value = runtime_config()["storage"].get(name)
    if not value or not Path(value).is_absolute():
        raise RuntimeError(f"storage.{name}은 절대경로여야 합니다.")
    return Path(value)
