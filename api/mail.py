from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage

from api.graph import send_graph_mail

try:
    from dotenv import load_dotenv
except ImportError:

    def load_dotenv() -> None:
        return None


load_dotenv()


def send_password_reset_email(email: str, token: str) -> None:
    if os.getenv("GRAPH_MAIL_ENABLED", "true").lower() == "true":
        reset_url = os.getenv("APP_PASSWORD_RESET_URL", "http://localhost:4200/reset-password")
        send_graph_mail(
            to=email,
            subject="Reinitialisation du mot de passe FoxRunner",
            body=f"Pour definir un nouveau mot de passe, utilisez ce token:\n\n{token}\n\n{reset_url}",
        )
        return

    host = os.getenv("SMTP_HOST")
    if not host:
        return
    port = int(os.getenv("SMTP_PORT", "587"))
    username = os.getenv("SMTP_USERNAME")
    password = os.getenv("SMTP_PASSWORD")
    sender = os.getenv("SMTP_FROM", username or "no-reply@localhost")
    reset_url = os.getenv("APP_PASSWORD_RESET_URL", "http://localhost:4200/reset-password")

    message = EmailMessage()
    message["Subject"] = "Reinitialisation du mot de passe FoxRunner"
    message["From"] = sender
    message["To"] = email
    message.set_content(f"Pour definir un nouveau mot de passe, utilisez ce token:\n\n{token}\n\n{reset_url}")

    with smtplib.SMTP(host, port, timeout=20) as smtp:
        if os.getenv("SMTP_STARTTLS", "true").lower() == "true":
            smtp.starttls()
        if username and password:
            smtp.login(username, password)
        smtp.send_message(message)
