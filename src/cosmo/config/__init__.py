"""Configuration model and loader."""

from cosmo.config.loader import DEFAULTS_PATH, load_config, user_config_path
from cosmo.config.model import CosmoConfig

__all__ = ["CosmoConfig", "load_config", "user_config_path", "DEFAULTS_PATH"]
