"""Central settings. Everything is path-relative so the tree is portable to an air-gapped box."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    ollama_host: str = "http://127.0.0.1:11434"
    workbench_data_dir: Path = ROOT / "data"
    workbench_offline: bool = True
    profile: str = "venue"
    ontology: str = "general"   # comma-separated packs from config/ontology.yaml

    @property
    def corpus_dir(self) -> Path:
        return self.workbench_data_dir / "corpus"

    @property
    def workspace_dir(self) -> Path:
        return self.workbench_data_dir / "workspace"

    @property
    def artifacts_dir(self) -> Path:
        return self.workbench_data_dir / "artifacts"

    @property
    def stores_dir(self) -> Path:
        return self.workbench_data_dir / "stores"

    def ensure_dirs(self) -> None:
        for p in (self.corpus_dir, self.workspace_dir, self.artifacts_dir, self.stores_dir):
            p.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s


@lru_cache(maxsize=1)
def get_manifest() -> dict:
    with open(ROOT / "config" / "models.yaml") as fh:
        return yaml.safe_load(fh)
