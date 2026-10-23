"""Envoi des quittances : SMTP (toute boîte) ou Microsoft Graph (Outlook.com / Microsoft 365)."""
from __future__ import annotations

import base64
import json
import logging
import os
import smtplib
import ssl
import urllib.error
import urllib.request
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from . import paths, secrets
from .store import Bailleur

log = logging.getLogger(__name__)

PRESETS = {
    "gmail":   {"label": "Gmail", "host": "smtp.gmail.com", "port": 587, "security": "starttls",
                "help": "Mot de passe d’application Google (validation en 2 étapes requise) : myaccount.google.com/apppasswords"},
    "ovh":     {"label": "OVH (adresse de votre domaine)", "host": "ssl0.ovh.net", "port": 465, "security": "ssl",
                "help": "Mot de passe de la boîte email OVH."},
    "orange":  {"label": "Orange", "host": "smtp.orange.fr", "port": 465, "security": "ssl", "help": "Mot de passe de la messagerie Orange."},
    "free":    {"label": "Free", "host": "smtp.free.fr", "port": 465, "security": "ssl", "help": "Mot de passe de la boîte Free."},
    "sfr":     {"label": "SFR", "host": "smtp.sfr.fr", "port": 465, "security": "ssl", "help": "Mot de passe de la boîte SFR."},
    "laposte": {"label": "La Poste", "host": "smtp.laposte.net", "port": 465, "security": "ssl", "help": "Mot de passe de la boîte La Poste."},
    "yahoo":   {"label": "Yahoo", "host": "smtp.mail.yahoo.com", "port": 465, "security": "ssl",
                "help": "Mot de passe d’application Yahoo (Sécurité du compte → Générer un mot de passe d’application)."},
    "icloud":  {"label": "iCloud", "host": "smtp.mail.me.com", "port": 587, "security": "starttls",
                "help": "Mot de passe pour app spécifique (appleid.apple.com)."},
    "custom":  {"label": "Autre (réglage manuel)", "host": "", "port": 465, "security": "ssl", "help": "Paramètres SMTP fournis par votre messagerie."},
    "outlook": {"label": "Outlook.com / Hotmail / Microsoft 365", "host": "", "port": 0, "security": "",
                "help": "Connexion avec votre compte Microsoft (aucun mot de passe stocké)."},
}

GRAPH_SCOPES = ["Mail.Send"]
AUTHORITY = "https://login.microsoftonline.com/common"
# Identifiant public de l'application Microsoft « Quittance Express » (non secret).
DEFAULT_MS_CLIENT_ID = ""


class MailError(Exception):
    """Erreur d'envoi présentable à l'utilisateur."""


def is_configured(b: Bailleur) -> bool:
    if b.mail_provider == "outlook":
        return bool(ms_client_id(b) and b.ms_account)
    return bool(b.mail_provider and b.smtp_host and b.smtp_user and secrets.get_smtp_password(b.smtp_user, b.smtp_host))


def sender_address(b: Bailleur) -> str:
    if b.mail_provider == "outlook":
        return b.ms_account
    return b.from_email or b.smtp_user


def _one_line(s: str) -> str:
    return " ".join(str(s or "").split())


def build_message(b: Bailleur, to: str, subject: str, body: str, attachment: bytes | None, filename: str | None,
                  cc: str | None = None) -> EmailMessage:
    frm = _one_line(sender_address(b))
    to, subject = _one_line(to), _one_line(subject)
    cc = _one_line(cc) if cc else None
    msg = EmailMessage()
    msg["From"] = formataddr((_one_line(b.nom), frm))
    msg["To"] = to
    if cc and cc.lower() not in (to.lower(), frm.lower()):
        msg["Cc"] = cc
    reply = _one_line(b.email)
    if reply and "," not in reply and reply.lower() != frm.lower():
        msg["Reply-To"] = reply
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain=(frm.split("@")[-1] or "quittance.local"))
    msg.set_content(body)
    if attachment:
        msg.add_attachment(attachment, maintype="application", subtype="pdf", filename=filename or "quittance.pdf")
    return msg


# ---------------- SMTP ----------------

def _login(server: smtplib.SMTP, user: str, pwd: str) -> None:
    if pwd.isascii() and user.isascii():
        server.login(user, pwd)
        return
    # smtplib n'accepte que l'ASCII : AUTH PLAIN encodé en UTF-8 (accepté par la plupart des serveurs)
    server.ehlo_or_helo_if_needed()
    token = base64.b64encode(("\0" + user + "\0" + pwd).encode("utf-8")).decode("ascii")
    code, resp = server.docmd("AUTH", "PLAIN " + token)
    if code not in (235, 503):
        raise smtplib.SMTPAuthenticationError(code, resp)


def _smtp_send(b: Bailleur, msg: EmailMessage, password: str | None = None) -> None:
    pwd = password if password is not None else secrets.get_smtp_password(b.smtp_user, b.smtp_host)
    if not pwd:
        raise MailError("Mot de passe de messagerie manquant : renseignez-le dans « Messagerie ».")
    if b.smtp_security not in ("ssl", "starttls") and not os.environ.get("QUITTANCE_ALLOW_PLAIN_SMTP"):
        raise MailError("Connexion non chiffrée refusée : choisissez SSL/TLS ou STARTTLS.")
    ctx = ssl.create_default_context()
    server = None
    try:
        if b.smtp_security == "ssl":
            server = smtplib.SMTP_SSL(b.smtp_host, b.smtp_port or 465, context=ctx, timeout=30)
        else:
            server = smtplib.SMTP(b.smtp_host, b.smtp_port or 587, timeout=30)
            server.ehlo()
            if b.smtp_security == "starttls":
                server.starttls(context=ctx)
                server.ehlo()
        _login(server, b.smtp_user, pwd)
        server.send_message(msg)
    except smtplib.SMTPAuthenticationError as e:
        raise MailError("Identifiant ou mot de passe refusé par le serveur de messagerie"
                        + (" (Gmail : utilisez un mot de passe d’application)." if "gmail" in b.smtp_host else ".")) from e
    except (smtplib.SMTPException, OSError, ValueError, UnicodeError) as e:
        raise MailError(f"Envoi impossible ({b.smtp_host}) : {e}") from e
    finally:
        # le message est parti : une réponse anormale à la déconnexion ne doit pas le faire passer pour un échec
        if server is not None:
            try:
                server.quit()
            except (smtplib.SMTPException, OSError):
                pass
            server.close()


# ---------------- Microsoft Graph ----------------

def ms_client_id(b: Bailleur) -> str:
    return (b.ms_client_id or DEFAULT_MS_CLIENT_ID).strip()


def _ms_app(b: Bailleur):
    try:
        import msal
    except ImportError as e:
        raise MailError("Module de connexion Microsoft indisponible.") from e

    cid = ms_client_id(b)
    if not cid:
        raise MailError("Identifiant d’application Microsoft manquant (voir « Messagerie »).")
    cache = _ms_cache()
    return msal.PublicClientApplication(cid, authority=AUTHORITY, token_cache=cache)


def _ms_cache():
    path = str(paths.data_dir() / "msal_cache.bin")
    try:
        from msal_extensions import PersistedTokenCache, build_encrypted_persistence

        return PersistedTokenCache(build_encrypted_persistence(path))  # DPAPI sous Windows
    except Exception as e:
        raise MailError(f"Stockage sécurisé de la connexion Microsoft indisponible : {e}") from e


def ms_login(b: Bailleur) -> str:
    """Connexion interactive (ouvre le navigateur). Retourne l'adresse du compte."""
    app = _ms_app(b)
    res = app.acquire_token_interactive(GRAPH_SCOPES, prompt="select_account", timeout=300)
    if "access_token" not in res:
        raise MailError("Connexion Microsoft refusée : " + str(res.get("error_description") or res.get("error")))
    claims = res.get("id_token_claims") or {}
    return claims.get("preferred_username") or claims.get("email") or ""


def ms_logout(b: Bailleur) -> None:
    try:
        app = _ms_app(b)
        for acc in app.get_accounts():
            app.remove_account(acc)
    except MailError:
        pass


def _ms_token(b: Bailleur) -> str:
    app = _ms_app(b)
    accounts = app.get_accounts(username=b.ms_account) or app.get_accounts()
    res = app.acquire_token_silent(GRAPH_SCOPES, account=accounts[0]) if accounts else None
    if not res or "access_token" not in res:
        raise MailError("Session Microsoft expirée : reconnectez-vous dans « Messagerie ».")
    return res["access_token"]


def _graph_send(b: Bailleur, msg: EmailMessage) -> None:
    token = _ms_token(b)

    def rcpts(h):
        return [{"emailAddress": {"address": a.strip()}} for a in (msg.get(h) or "").split(",") if a.strip()]

    attachments = []
    for part in msg.iter_attachments():
        attachments.append({
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": part.get_filename(),
            "contentType": part.get_content_type(),
            "contentBytes": base64.b64encode(part.get_payload(decode=True)).decode(),
        })
    body = msg.get_body(preferencelist=("plain",)).get_content()
    payload = {"message": {"subject": str(msg["Subject"]), "body": {"contentType": "Text", "content": body},
                           "toRecipients": rcpts("To"), "ccRecipients": rcpts("Cc"),
                           "replyTo": rcpts("Reply-To"), "attachments": attachments},
               "saveToSentItems": True}
    req = urllib.request.Request("https://graph.microsoft.com/v1.0/me/sendMail", data=json.dumps(payload).encode(),
                                 headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            if r.status not in (200, 202):
                raise MailError(f"Outlook a répondu {r.status}.")
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise MailError(f"Outlook a refusé l’envoi ({e.code}) : {detail}") from e
    except urllib.error.URLError as e:
        raise MailError(f"Connexion à Outlook impossible : {e.reason}") from e
    except (TimeoutError, OSError) as e:
        raise MailError(f"Connexion à Outlook impossible : {e}") from e


def send(b: Bailleur, msg: EmailMessage, password: str | None = None) -> None:
    if b.mail_provider == "outlook":
        _graph_send(b, msg)
    elif b.mail_provider:
        _smtp_send(b, msg, password)
    else:
        raise MailError("Aucune messagerie configurée : ouvrez « Messagerie ».")
