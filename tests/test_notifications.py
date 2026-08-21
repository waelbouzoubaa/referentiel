from __future__ import annotations

from middleware.core.config import get_settings
from middleware.notifications import send_support_notification


def test_send_support_notification_noop_sans_configuration(monkeypatch):
    """Sans SMTP host ni destinataire configurés, la fonction ne fait rien et ne lève pas."""
    settings = get_settings()
    monkeypatch.setattr(settings, "smtp_host", "")
    monkeypatch.setattr(settings, "support_notify_email", "")

    send_support_notification("sujet", "corps")


def test_send_support_notification_smtp_injoignable_ne_leve_pas(monkeypatch):
    """Un serveur SMTP injoignable est capturé (best-effort), pas de levée d'exception."""
    settings = get_settings()
    monkeypatch.setattr(settings, "smtp_host", "127.0.0.1")
    monkeypatch.setattr(settings, "smtp_port", 1)
    monkeypatch.setattr(settings, "support_notify_email", "support@ramery.fr")

    send_support_notification("sujet", "corps")
