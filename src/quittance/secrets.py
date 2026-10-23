"""Secrets : Gestionnaire d'identifiants Windows (via keyring). Jamais en clair dans un fichier."""
from __future__ import annotations

import logging

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

SERVICE = "QuittanceExpress"
log = logging.getLogger(__name__)


def _key(user: str, host: str) -> str:
    return f"smtp:{user.lower()}@{host.lower()}"


def set_smtp_password(user: str, host: str, password: str) -> None:
    keyring.set_password(SERVICE, _key(user, host), password)


def get_smtp_password(user: str, host: str) -> str | None:
    try:
        return keyring.get_password(SERVICE, _key(user, host))
    except KeyringError as e:
        log.warning("Lecture du mot de passe impossible : %s", e)
        return None


def delete_smtp_password(user: str, host: str) -> None:
    try:
        keyring.delete_password(SERVICE, _key(user, host))
    except (PasswordDeleteError, KeyringError):
        pass
