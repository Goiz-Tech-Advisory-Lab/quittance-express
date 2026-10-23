import email
from datetime import date

import pytest

from quittance import importer, scheduler, service, store


def _msg(envelope):
    return email.message_from_bytes(envelope.content)


def test_send_manual_with_attachment_and_copy(configured):
    box, bail = configured
    e = service.send(bail.id, "2026-10")
    assert e["statut"] == "envoyee" and e["destinataire"] == "helene@test.fr"
    assert len(box.messages) == 1
    env = box.messages[0]
    assert set(env.rcpt_tos) == {"helene@test.fr", "proprio@test.fr"}
    m = _msg(env)
    assert "octobre 2026" in str(email.header.make_header(email.header.decode_header(m["Subject"])))
    atts = [p for p in m.walk() if p.get_filename()]
    assert atts and atts[0].get_filename() == "quittance_MARTIN-LEFEVRE_2026-10.pdf"
    assert atts[0].get_payload(decode=True)[:5] == b"%PDF-"
    body = next(p for p in m.walk() if p.get_content_type() == "text/plain").get_payload(decode=True).decode()
    assert "Bonjour Hélène" in body and "d’octobre 2026" in body and "920,50 €" in body
    assert m["Reply-To"] == "proprio@test.fr"


def test_idempotence_and_force(configured):
    box, bail = configured
    service.send(bail.id, "2026-10")
    with pytest.raises(service.ServiceError, match="déjà envoyée"):
        service.send(bail.id, "2026-10")
    service.send(bail.id, "2026-10", force=True)
    assert len(box.messages) == 2


def test_suspend_blocks_send(configured):
    box, bail = configured
    service.suspend(bail.id, "2026-10")
    with pytest.raises(service.ServiceError, match="loyer non reçu"):
        service.send(bail.id, "2026-10")
    assert service.unsuspend(bail.id, "2026-10")
    service.send(bail.id, "2026-10")
    assert len(box.messages) == 1


def test_wrong_password_records_failure(configured):
    from quittance import secrets
    box, bail = configured
    secrets.set_smtp_password("bailleur@test.fr", "127.0.0.1", "faux")
    with pytest.raises(service.ServiceError, match="refusé"):
        service.send(bail.id, "2026-10")
    assert store.load_history()[-1]["statut"] == "echec"
    assert store.month_status(bail.id, "2026-10") is None  # un échec ne bloque pas le prochain essai


def test_scheduler_rules(configured):
    box, bail = configured
    assert scheduler.due(date(2026, 10, 15)) == []          # auto désactivé par défaut
    bail.auto = True
    bail.jour_envoi = 10
    store.upsert_bail(bail)
    assert scheduler.due(date(2026, 10, 9)) == []           # avant le jour d'envoi
    assert [m for _, m in scheduler.due(date(2026, 10, 10))] == ["2026-10"]
    s = scheduler.run_due(date(2026, 10, 12))
    assert len(s["envoyees"]) == 1 and len(box.messages) == 1
    s = scheduler.run_due(date(2026, 10, 20))               # jamais deux fois le même mois
    assert s["envoyees"] == [] and len(box.messages) == 1
    assert store.load_history()[-1]["origine"] == "auto"


def test_scheduler_short_month_suspension_and_start(configured):
    box, bail = configured
    bail.auto, bail.jour_envoi = True, 31
    store.upsert_bail(bail)
    assert scheduler.send_day(bail, 2027, 2) == 28
    service.send(bail.id, "2027-01")
    assert [m for _, m in scheduler.due(date(2027, 2, 28))] == ["2027-02"]
    service.suspend(bail.id, "2027-02")
    assert scheduler.due(date(2027, 2, 28)) == []
    bail.debut = "2027-06"
    store.upsert_bail(bail)
    assert scheduler.due(date(2027, 3, 31)) == []


def test_scheduler_needs_mail_and_lock(configured, isolated):
    import subprocess, sys, time
    box, bail = configured
    bail.auto = True
    store.upsert_bail(bail)
    # un autre processus détient le verrou d'envoi automatique
    code = ("import sys,time; sys.path.insert(0, %r); from quittance import store\n"
            "with store.file_lock('auto'):\n    print('ok', flush=True); time.sleep(4)") % str(__import__('pathlib').Path(__file__).resolve().parent.parent / 'src')
    p = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, env={**__import__('os').environ})
    assert p.stdout.readline().strip() == b"ok"
    s = scheduler.run_due(date(2026, 10, 15))
    p.wait()
    assert "déjà en cours" in s["ignore"] and box.messages == []
    b = store.Bailleur.load()
    b.mail_provider = ""
    b.save()
    s = scheduler.run_due(date(2026, 10, 15))
    assert s["ignore"] == "Messagerie non configurée." and box.messages == []


def test_missed_months(configured):
    box, bail = configured
    bail.auto, bail.debut = True, "2026-08"
    store.upsert_bail(bail)
    service.send(bail.id, "2026-09")
    missed = scheduler.missed(date(2026, 10, 15))
    assert [m["mois"] for m in missed] == ["2026-08"]


def test_import_site_templates():
    data = {"app": "quittance-goiztechadvisory", "templates": {
        "Lyon – Martin": {"bailleurNom": "SCI X", "bailleurAdresse": "1 rue A", "locPrenom": "Marie", "locNom": "Martin",
                          "locEmail": "m@x.fr", "logementAdresse": "Lyon", "loyer": "850,50", "charges": "70",
                          "modePaiement": "virement", "faitA": "Paris", "signature": ""},
        "Autre": {"locNom": "Durand", "loyer": "abc", "modePaiement": "bitcoin"}}}
    r = importer.import_json(data)
    assert r["baux"] == 2
    baux = {b.loc_nom: b for b in store.load_baux()}
    assert baux["Martin"].loyer == 85050 and baux["Martin"].charges == 7000 and baux["Martin"].mode_paiement == "virement"
    assert baux["Durand"].loyer == 0 and baux["Durand"].mode_paiement == ""
    assert store.Bailleur.load().nom == "SCI X" and store.Bailleur.load().ville == "Paris"


def test_backup_roundtrip(configured):
    box, bail = configured
    dump = importer.export_backup()
    assert "secret" not in str(dump)
    store.save_baux([])
    importer.import_json(dump)
    assert store.get_bail(bail.id).loc_email == "helene@test.fr"
