from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VERSIONS_LOCK = REPO_ROOT / "versions.lock.yaml"
LOCAL_COMPOSE = REPO_ROOT / "infra" / "local" / "compose.yaml"
