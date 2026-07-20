"""BRAIN API 访问配置管理。"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv


@dataclass
class SimulationDefaults:
    """默认模拟参数。"""
    instrument_type: str = "EQUITY"
    region: str = "USA"
    universe: str = "TOP3000"
    delay: int = 1
    decay: int = 13
    neutralization: str = "INDUSTRY"
    truncation: float = 0.13
    pasteurization: str = "ON"
    unit_handling: str = "VERIFY"
    nan_handling: str = "OFF"
    language: str = "FASTEXPR"
    visualization: bool = False

    def to_dict(self) -> dict:
        """转换为 BRAIN API 所需的设置字典。"""
        return {
            "instrumentType": self.instrument_type,
            "region": self.region,
            "universe": self.universe,
            "delay": self.delay,
            "decay": self.decay,
            "neutralization": self.neutralization,
            "truncation": self.truncation,
            "pasteurization": self.pasteurization,
            "unitHandling": self.unit_handling,
            "nanHandling": self.nan_handling,
            "language": self.language,
            "visualization": self.visualization,
        }


@dataclass
class Config:
    """应用配置。"""
    email: str = ""
    password: str = ""
    simulation: SimulationDefaults = field(default_factory=SimulationDefaults)
    max_concurrency: int = 8
    multi_alpha_batch_size: int = 10
    log_level: str = "INFO"
    log_dir: Path = Path(".local/logs/wqb")
    request_max_attempts: int = 3
    request_backoff_seconds: float = 1.0


def load_config(env_path: str | None = None) -> Config:
    """从环境变量加载配置。"""
    load_dotenv(env_path or ".env")

    sim = SimulationDefaults(
        region=os.getenv("WQB_REGION", "USA"),
        universe=os.getenv("WQB_UNIVERSE", "TOP3000"),
        delay=int(os.getenv("WQB_DELAY", "1")),
        decay=int(os.getenv("WQB_DECAY", "13")),
        neutralization=os.getenv("WQB_NEUTRALIZATION", "INDUSTRY"),
        truncation=float(os.getenv("WQB_TRUNCATION", "0.13")),
    )

    return Config(
        email=os.getenv("WQB_EMAIL", ""),
        password=os.getenv("WQB_PASSWORD", ""),
        simulation=sim,
        max_concurrency=int(os.getenv("WQB_MAX_CONCURRENCY", "8")),
        multi_alpha_batch_size=int(os.getenv("WQB_MULTI_ALPHA_BATCH_SIZE", "10")),
        log_level=os.getenv("WQB_LOG_LEVEL", "INFO"),
        log_dir=Path(os.getenv("WQB_LOG_DIR", ".local/logs/wqb")),
        request_max_attempts=int(os.getenv("WQB_REQUEST_MAX_ATTEMPTS", "3")),
        request_backoff_seconds=float(os.getenv("WQB_REQUEST_BACKOFF_SECONDS", "1.0")),
    )
