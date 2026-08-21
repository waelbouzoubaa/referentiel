"""Notifications email — point d'intégration unique et remplaçable.

Aujourd'hui : un seul cas d'usage (alerter le support quand une demande de
validation passe en file "Aide support"). Envoi best-effort : un problème SMTP
ne doit jamais faire échouer l'action métier qui déclenche la notification.
"""
from __future__ import annotations

import smtplib
from email.message import EmailMessage

from middleware.core.config import get_settings
from middleware.core.logging import get_logger

logger = get_logger(__name__)


def send_support_notification(subject: str, body: str) -> None:
    """Envoie un email au destinataire configuré (MIDDLEWARE_SUPPORT_NOTIFY_EMAIL).

    Ne lève jamais : si le SMTP ou le destinataire ne sont pas configurés, ou si
    l'envoi échoue, l'erreur est journalisée et la fonction retourne silencieusement.
    """
    settings = get_settings()
    if not settings.support_notify_email or not settings.smtp_host:
        logger.warning(
            "notification support ignorée — MIDDLEWARE_SMTP_HOST ou "
            "MIDDLEWARE_SUPPORT_NOTIFY_EMAIL non configuré"
        )
        return

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from or settings.smtp_user or "middleware@ramery.fr"
    msg["To"] = settings.support_notify_email
    msg.set_content(body)

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as server:
            if settings.smtp_use_tls:
                server.starttls()
            if settings.smtp_user:
                server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)
        logger.info("notification support envoyée", destinataire=settings.support_notify_email)
    except Exception as exc:
        logger.warning("échec envoi notification support", erreur=str(exc))
