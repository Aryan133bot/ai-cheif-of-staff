"""Email provider abstraction layer for multi-provider support."""

from .base import EmailProvider
from .registry import ProviderRegistry

__all__ = ["EmailProvider", "ProviderRegistry"]
