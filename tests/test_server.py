import http.client
import json

import pytest

from quittance import server, store


@pytest.fixture
def srv():
    httpd, url = server.start()
    port = httpd.server_address[1]
    yield httpd, port
    httpd.shutdown()


def call(port, path, data=None, token=None, host=None, method="POST"):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    headers = {"Content-Type": "application/json", "Host": host or f"127.0.0.1:{port}"}
    if token:
        headers["X-Token"] = token
    c.request(method, path, body=json.dumps(data or {}) if method == "POST" else None, headers=headers)
    r = c.getresponse()
    body = r.read()
    try:
        return r.status, json.loads(body)
    except ValueError:
        return r.status, body


def test_security(srv):
    httpd, port = srv
    assert call(port, "/api/state")[0] == 403                                   # sans jeton
    assert call(port, "/api/state", token="mauvais")[0] == 403
    assert call(port, "/api/state", token=httpd.token, host="evil.example:80")[0] == 403  # DNS rebinding
    assert call(port, "/api/_clean", token=httpd.token)[0] == 404
    st, j = call(port, "/api/state", token=httpd.token)
    assert st == 200 and j["ok"]
    assert call(port, "/", method="GET")[0] == 200
    assert call(port, "/../server.py", method="GET")[0] == 404
    assert call(port, "/", method="GET", host="evil.example")[0] == 403


def test_bail_flow_and_preview(srv):
    httpd, port = srv
    t = httpd.token
    call(port, "/api/bailleur", {"nom": "SCI Test", "ville": "Paris"}, t)
    st, j = call(port, "/api/bail-save", {"loc_nom": "Martin", "logement_adresse": "Lyon", "loyer": "850,50", "charges": "70"}, t)
    assert st == 200 and j["bail"]["total"] == 92050 and j["bail"]["auto"] is False and j["bail"]["debut"]
    bid = j["bail"]["id"]
    st, j = call(port, "/api/preview", {"id": bid, "mois": "2026-10"}, t)
    assert st == 200 and j["image"].startswith("data:image/png;base64,") and j["lettres"].startswith("neuf cent vingt euros")
    st, j = call(port, "/api/generate", {"id": bid, "mois": "2026-10"}, t)
    assert st == 200 and j["fichier"].endswith("quittance_MARTIN_2026-10.pdf")
    assert call(port, "/api/bail-save", {"loc_nom": "X", "loyer": "-5"}, t)[0] == 400
    assert call(port, "/api/bail-save", {"loc_nom": "X", "loyer": "5", "auto": True}, t)[0] == 400  # auto sans email
    assert call(port, "/api/send", {"id": bid, "mois": "2026-13"}, t)[0] == 400
    assert call(port, "/api/open", {"path": "/etc/passwd"}, t)[0] == 400


def test_mail_config_never_echoes_password(srv):
    httpd, port = srv
    t = httpd.token
    st, j = call(port, "/api/mail-config", {"provider": "gmail", "user": "moi@gmail.com", "password": "abcd efgh"}, t)
    assert st == 200
    st, j = call(port, "/api/state", token=t)
    assert "abcd efgh" not in json.dumps(j) and j["has_password"] is True
    assert "abcd" not in (store.paths.data_dir() / "bailleur.json").read_text()
