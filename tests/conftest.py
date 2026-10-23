import sys
from pathlib import Path

import keyring
import pytest
from keyring.backend import KeyringBackend

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self):
        super().__init__()
        self.store = {}

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def get_password(self, service, username):
        return self.store.get((service, username))

    def delete_password(self, service, username):
        self.store.pop((service, username), None)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("QUITTANCE_HOME", str(tmp_path))
    monkeypatch.setenv("QUITTANCE_NO_OPEN", "1")
    monkeypatch.setenv("QUITTANCE_ALLOW_PLAIN_SMTP", "1")  # serveur SMTP de test sans TLS
    keyring.set_keyring(MemoryKeyring())
    yield tmp_path


@pytest.fixture
def smtp_server():
    """Serveur SMTP local (aiosmtpd) avec authentification, sans TLS."""
    from aiosmtpd.controller import Controller
    from aiosmtpd.smtp import AuthResult, LoginPassword

    class Box:
        messages = []

        async def handle_DATA(self, server, session, envelope):
            self.messages.append(envelope)
            return "250 OK"

    def auth(server, session, envelope, mechanism, auth_data):
        ok = isinstance(auth_data, LoginPassword) and auth_data.login == b"bailleur@test.fr" and auth_data.password in (b"secret", "mötdepasse".encode())
        return AuthResult(success=ok, handled=False, message=None if ok else "535 5.7.8 Authentication credentials invalid")

    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    box = Box()
    box.messages = []
    c = Controller(box, hostname="127.0.0.1", port=port, authenticator=auth, auth_require_tls=False)
    c.start()
    yield box, port
    c.stop()


@pytest.fixture
def configured(smtp_server):
    """Bailleur + messagerie SMTP de test + un bail."""
    from quittance import secrets, store

    box, port = smtp_server
    b = store.Bailleur(nom="SCI Les Tilleuls", adresse="12 rue des Lilas\n75011 Paris", email="proprio@test.fr", ville="Paris",
                       mail_provider="custom", smtp_host="127.0.0.1", smtp_port=port, smtp_security="none",
                       smtp_user="bailleur@test.fr")
    b.save()
    secrets.set_smtp_password("bailleur@test.fr", "127.0.0.1", "secret")
    bail = store.upsert_bail(store.Bail(loc_prenom="Hélène", loc_nom="Martin-Lefèvre", loc_email="helene@test.fr",
                                        logement_adresse="3 av Jean Jaurès\n69007 Lyon", loyer=85050, charges=7000,
                                        mode_paiement="virement", jour_paiement=5, debut="2026-01", accord_email=True))
    return box, bail
