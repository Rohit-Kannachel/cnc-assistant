"""Shared Flask extensions, created here so models and routes can import them without cycles."""

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
