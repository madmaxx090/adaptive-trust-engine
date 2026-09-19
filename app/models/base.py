"""Declarative base shared by all ATE SQLAlchemy models."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for the ATE data-layer models."""
