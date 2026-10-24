import hashlib

import yaml

from m0.paths import VERSIONS_LOCK

REQUIRED_IMAGES = ("trino", "catalog", "catalog_db", "objstore", "cache_drop")


def load() -> dict:
    with VERSIONS_LOCK.open() as f:
        data = yaml.safe_load(f)
    missing = [k for k in REQUIRED_IMAGES if k not in data.get("images", {})]
    if missing:
        raise ValueError(f"versions.lock.yaml is missing images: {', '.join(missing)}")
    return data


def lock_hash() -> str:
    return hashlib.sha256(VERSIONS_LOCK.read_bytes()).hexdigest()[:12]


def compose_env(versions: dict) -> dict[str, str]:
    """Image names for infra/local/compose.yaml, as M0_IMAGE_<NAME> variables."""
    return {f"M0_IMAGE_{name.upper()}": image for name, image in versions["images"].items()}
