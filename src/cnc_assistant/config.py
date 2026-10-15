"""App configuration, read from environment variables (and a .env file if present)."""

import os
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

# Look for .env starting from the directory the app is launched in, walking upward.
load_dotenv(find_dotenv(usecwd=True))


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-not-secret")

    # Claude planner (Phase 3)
    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
    CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")

    # Data locations. Everything the app writes lives under DATA_DIR.
    DATA_DIR = Path(os.environ.get("CNC_DATA_DIR", "instance")).resolve()
    UPLOAD_DIR = DATA_DIR / "uploads"
    GCODE_DIR = DATA_DIR / "gcode"
    TOOL_IMAGE_DIR = DATA_DIR / "tool_images"
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # largest upload accepted (photos, STLs), in bytes
    SQLALCHEMY_DATABASE_URI = f"sqlite:///{(DATA_DIR / 'cnc_assistant.db').as_posix()}"

    # Web server
    HOST = os.environ.get("CNC_HOST", "127.0.0.1")
    PORT = int(os.environ.get("CNC_PORT", "5000"))

    # Machine limits: Genmitsu 3018 Pro
    MACHINE_NAME = "Genmitsu 3018 Pro"
    BED_X_MM = 300.0
    BED_Y_MM = 180.0
    SAFE_Z_MM = 5.0
