"""Central env-var bootstrap and runtime config for BookScan.

Anything that needs to be set BEFORE third-party imports (torch, surya,
transformers) lives in ``init_env()``. Everything else is a constant.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).parent
PROJECT_DIR = BACKEND_DIR.parent
OUTPUTS_DIR = PROJECT_DIR / "outputs"
LOGS_DIR = BACKEND_DIR / "logs"

BUNDLED_LLAMA = BACKEND_DIR / "llamacpp" / "llama-server.exe"

MAX_UPLOAD = 5

# CORS: open to all by default (deployment target is RunPod with no auth — the
# frontend lives on the user's PC and hits the public RunPod URL across the
# internet). Override with BOOKSCAN_CORS_ORIGINS="https://a,https://b" if you
# want to lock it down later.
_origins_env = os.environ.get("BOOKSCAN_CORS_ORIGINS", "").strip()
if _origins_env:
    ALLOWED_ORIGINS = [o.strip() for o in _origins_env.split(",") if o.strip()]
    ALLOWED_ORIGIN_REGEX = None
else:
    ALLOWED_ORIGINS = ["*"]
    ALLOWED_ORIGIN_REGEX = None

LOG_FILE = LOGS_DIR / "bookscan.log"
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 5


def init_env() -> None:
    """Set every os.environ entry Surya / torch need. Idempotent.

    CPU forcing applies only on Windows (local dev machine) where we hit the
    meta-tensor init bug. On Linux (RunPod), let torch use CUDA if available.
    Override either way by setting BOOKSCAN_FORCE_CPU=1 or BOOKSCAN_FORCE_GPU=1.
    """
    if BUNDLED_LLAMA.exists() and not os.environ.get("LLAMA_CPP_BINARY"):
        os.environ["LLAMA_CPP_BINARY"] = str(BUNDLED_LLAMA)

    force_cpu = os.environ.get("BOOKSCAN_FORCE_CPU") == "1"
    force_gpu = os.environ.get("BOOKSCAN_FORCE_GPU") == "1"
    is_windows = sys.platform == "win32"

    if force_gpu:
        # Strip any leftover CPU pins so torch can see the GPU.
        for k in ("TORCH_DEVICE", "SURYA_TORCH_DEVICE", "CUDA_VISIBLE_DEVICES"):
            os.environ.pop(k, None)
        return

    if force_cpu or is_windows:
        os.environ.setdefault("TORCH_DEVICE", "cpu")
        os.environ.setdefault("SURYA_TORCH_DEVICE", "cpu")
        os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")


def init_logging() -> None:
    """Console + rotating file logging. Idempotent."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    # Avoid double-handlers under uvicorn --reload or test re-imports.
    if any(getattr(h, "_bookscan", False) for h in root.handlers):
        return

    fmt = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    console._bookscan = True  # type: ignore[attr-defined]

    file = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
    )
    file.setFormatter(fmt)
    file._bookscan = True  # type: ignore[attr-defined]

    root.setLevel(logging.INFO)
    root.addHandler(console)
    root.addHandler(file)
