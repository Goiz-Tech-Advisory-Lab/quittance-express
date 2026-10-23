"""Tests de non-régression des points relevés en relecture (robustesse, double envoi, validation)."""
import json
import smtplib
import subprocess
import sys
from datetime import date
from pathlib import Path

import pypdfium2 as pdfium
import pytest

from quittance import importer, paths, pdfgen, scheduler, service, store
from quittance.words import parse_amount

SRC = str(Path(__file__).resolve().parent.parent / "src")


def _pdf_text(path) -> str:
    doc = pdfium.PdfDocument(str(path))
    try:
        return doc[0].get_textpage().get_text_range().replace("\r\n", " ")
    finally:
        doc.close()


def test_transient_read_error_is_retried(configured, monkeypatch):
    box, bail = configured
    service.send(bail.id, "2026-10")
    real = Path.read_text
    calls = {"n": 0}

    def flaky(self, *a, **k):
        if self.name == "historique.json" and calls["n"] < 2:
            calls["n"] += 1
            raise PermissionError("verrouillé par l'antivirus")
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", flaky)
    assert store.month_status(bail.id, "2026-10")["statut"] == "envoyee"   # pas d'historique « vide »
    assert calls["n"] == 2


def test_persistent_read_error_never_wipes_history(configured, monkeypatch):
    box, bail = configured
    bail.auto = True
    store.upsert_bail(bail)
    service.send(bail.id, "2026-10")
    real = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: (_ for _ in ()).throw(PermissionError("x"))
                        if self.name == "historique.json" else real(self, *a, **k))
    s = scheduler.run_due(date(2026, 10, 20))
    assert s["envoyees"] == [] and len(box.messages) == 1 and "inaccessible" in s["ignore"]
    monkeypatch.setattr(Path, "read_text", real)
    assert len(store.load_history()) == 1                                  # rien n'a été écrasé


def test_corrupt_history_blocks_auto_and_is_kept(configured):
    box, bail = configured
    bail.auto = True
    store.upsert_bail(bail)
    h = paths.data_dir() / "historique.json"
    h.write_text("{ cassé", encoding="utf-8")
    s = scheduler.run_due(date(2026, 10, 20))
    assert s["envoyees"] == [] and box.messages == [] and "illisible" in s["ignore"]
    assert h.read_text(encoding="utf-8") == "{ cassé"                      # fichier conservé tel quel


def test_concurrent_processes_do_not_lose_history(isolated):
    code = ("import sys; sys.path.insert(0, %r)\n"
            "from quittance import store\n"
            "for i in range(60): store.add_history({'bail_id': 'x', 'mois': '2026-01', 'statut': 'generee', 'n': i})") % SRC
    procs = [subprocess.Popen([sys.executable, "-c", code]) for _ in range(3)]
    for p in procs:
        assert p.wait(timeout=120) == 0
    assert len(store.load_history()) == 180


def test_quit_error_after_send_still_counts_as_sent(configured, monkeypatch):
    box, bail = configured

    def bad_quit(self):
        raise smtplib.SMTPResponseException(421, b"bye")

    monkeypatch.setattr(smtplib.SMTP, "quit", bad_quit)
    e = service.send(bail.id, "2026-10")
    assert e["statut"] == "envoyee" and len(box.messages) == 1
    assert store.month_status(bail.id, "2026-10")["statut"] == "envoyee"


def test_one_failing_lease_does_not_stop_others(configured, monkeypatch):
    box, bail = configured
    bail.auto = True
    store.upsert_bail(bail)
    other = store.upsert_bail(store.Bail(loc_nom="Durand", loc_email="durand@test.fr", logement_adresse="Nantes",
                                         loyer=50000, auto=True, accord_email=True, debut="2026-01"))
    real = pdfgen.render

    def boom(q):
        if q.locataire.endswith("Martin-Lefèvre"):
            raise IndexError("panne")
        return real(q)

    monkeypatch.setattr(pdfgen, "render", boom)
    s = scheduler.run_due(date(2026, 10, 15))
    assert s["envoyees"] == ["Durand (2026-10)"] and len(s["echecs"]) == 1
    assert len(box.messages) == 1 and store.month_status(other.id, "2026-10")


def test_consent_required(configured):
    box, bail = configured
    bail.accord_email = False
    store.upsert_bail(bail)
    with pytest.raises(service.ServiceError, match="accord"):
        service.send(bail.id, "2026-10")
    with pytest.raises(service.ServiceError, match="accord"):
        service.bail_from({"auto": True}, bail)
    bail.auto = True  # même forcé dans le fichier, l'automatique ignore un bail sans accord
    store.upsert_bail(bail)
    assert scheduler.due(date(2026, 10, 15)) == []


def test_earlier_suspension_blocks_auto(configured):
    box, bail = configured
    bail.auto = True
    store.upsert_bail(bail)
    service.suspend(bail.id, "2026-09")
    assert scheduler.due(date(2026, 10, 15)) == []
    assert scheduler.blocked(date(2026, 10, 15))[0]["mois"] == ["2026-09"]
    service.unsuspend(bail.id, "2026-09")
    assert [m for _, m in scheduler.due(date(2026, 10, 15))] == ["2026-10"]


def test_auto_send_omits_payment_date_manual_keeps_it(configured):
    box, bail = configured
    manual = service.send(bail.id, "2026-10")
    bail.auto = True
    store.upsert_bail(bail)
    scheduler.run_due(date(2026, 11, 15))
    auto = store.month_status(bail.id, "2026-11")
    assert "Paiement reçu le 05/10/2026 par virement" in _pdf_text(manual["fichier"])
    t = _pdf_text(auto["fichier"])
    assert "Paiement reçu par virement" in t and "05/11/2026" not in t


def test_archive_never_overwrites(configured):
    box, bail = configured
    p1 = service.send(bail.id, "2026-10")["fichier"]
    p2 = service.generate(bail.id, "2026-10")
    assert Path(p1) != p2 and Path(p1).exists() and p2.name.endswith("-2.pdf")


def test_forfait_label(configured):
    box, bail = configured
    bail.type_charges = "forfait"
    store.upsert_bail(bail)
    t = _pdf_text(service.generate(bail.id, "2026-10"))
    assert "Charges (forfait)" in t and "Provision" not in t


def test_amount_validation():
    for bad in (True, -5, "-1", "1e12", "abc"):
        with pytest.raises(ValueError):
            parse_amount(bad)
    assert parse_amount(850.5) == 85050


def test_header_injection_and_utf8_password(configured):
    from quittance import secrets
    box, bail = configured
    b = store.Bailleur.load()
    b.nom = "SCI Test\r\nBcc: espion@x.fr"
    b.email = "proprio@test.fr"
    b.save()
    secrets.set_smtp_password("bailleur@test.fr", "127.0.0.1", "mötdepasse")
    service.send(bail.id, "2026-10")
    env = box.messages[0]
    assert "espion@x.fr" not in env.rcpt_tos
    import email
    m = email.message_from_bytes(env.content)
    assert m["Bcc"] is None and "espion" not in (m["To"] + (m["Cc"] or ""))


def test_hostile_backup_cannot_break_app(configured):
    box, bail = configured
    importer.import_json({"app": "quittance-express", "bailleur": {"smtp_security": "none", "email": "a@b.fr, c@d.fr",
                                                                   "mail_provider": "custom", "smtp_port": "abc"},
                          "baux": [{"id": "<img src=x>", "loc_nom": "X", "loyer": "850", "jour_envoi": "abc"},
                                   {"id": "abcdefabcdef", "loc_nom": "Y", "loyer": -5},
                                   {"loc_nom": "Z", "auto": True}],
                          "signature": "data:image/png;base64,QUJD"})
    b = store.Bailleur.load()
    assert b.smtp_security == "ssl" and b.email == "proprio@test.fr" and b.smtp_port == 0
    ids = [x.id for x in store.load_baux()]
    assert "<img src=x>" not in ids and "abcdefabcdef" not in ids
    assert all(isinstance(x.loyer, int) and x.loyer >= 0 for x in store.load_baux())
    scheduler.due(date(2026, 10, 15))                                        # ne plante pas
    from quittance.server import Api
    assert Api().state({})["baux"]                                           # l'interface se charge
