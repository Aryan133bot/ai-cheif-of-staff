"""Abstract base class for email providers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass
class FetchedEmail:
    """Normalized email from any provider."""
    email_id: str
    subject: str
    sender: str
    body: str
    received_at: datetime
    thread_id: str | None = None


class EmailProvider(ABC):
    """Base class for email providers (Gmail, Outlook, Yahoo, etc.).

    Each provider implements fetching emails and reporting auth status.
    The processing pipeline is provider-agnostic.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider identifier, e.g. 'gmail', 'outlook'."""
        ...

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable provider name, e.g. 'Google Gmail'."""
        ...

    @abstractmethod
    def is_configured(self) -> bool:
        """Check if the provider has the required credentials files."""
        ...

    @abstractmethod
    def is_authenticated(self) -> bool:
        """Check if the provider has a valid auth token."""
        ...

    @abstractmethod
    def fetch_emails(self, max_results: int = 50, mode: str = "unread") -> list[FetchedEmail]:
        """Fetch recent emails from this provider."""
        ...

    def get_status(self) -> dict:
        """Return the auth/connection status for this provider."""
        return {
            "provider": self.name,
            "display_name": self.display_name,
            "configured": self.is_configured(),
            "authenticated": self.is_authenticated(),
        }
