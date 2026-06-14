"""Provider registry for managing multiple email providers."""

import logging
from .base import EmailProvider, FetchedEmail

logger = logging.getLogger(__name__)


class ProviderRegistry:
    """Manages registered email providers and aggregates their results."""

    def __init__(self):
        self._providers: dict[str, EmailProvider] = {}

    def register(self, provider: EmailProvider) -> None:
        """Register an email provider."""
        self._providers[provider.name] = provider
        logger.info("Registered email provider: %s", provider.display_name)

    def get(self, name: str) -> EmailProvider | None:
        """Get a provider by name."""
        return self._providers.get(name)

    def list_providers(self) -> list[dict]:
        """Return status of all registered providers."""
        return [p.get_status() for p in self._providers.values()]

    def fetch_all(self, max_results: int = 50, mode: str = "unread") -> list[FetchedEmail]:
        """Fetch emails from all connected providers."""
        all_emails: list[FetchedEmail] = []
        for provider in self._providers.values():
            if not provider.is_configured():
                logger.info("Skipping %s — not configured", provider.display_name)
                continue
            try:
                emails = provider.fetch_emails(max_results=max_results, mode=mode)
                logger.info("Fetched %d emails from %s", len(emails), provider.display_name)
                all_emails.extend(emails)
            except Exception as e:
                logger.error("Failed to fetch from %s: %s", provider.display_name, e)
        return all_emails
