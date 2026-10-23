"""Parcours complet dans l'interface (navigateur Chromium via Playwright)."""
import os
from pathlib import Path

import pytest

pw = pytest.importorskip("playwright.sync_api")
expect = pw.expect

SHOTS = os.environ.get("QUITTANCE_SHOTS")  # dossier de captures (optionnel)


def shot(page, name):
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / f"{name}.png"))


@pytest.fixture
def ui(smtp_server):
    from quittance import server

    httpd, url = server.start()
    with pw.sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1440, "height": 920})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(url)
        page.wait_for_selector("#version:not(:empty)")
        yield page, smtp_server, errors
        b.close()
    httpd.shutdown()


def test_full_flow(ui, tmp_path):
    page, (box, port), errors = ui
    from quittance import secrets, store

    # 1er lancement : état vide + alertes
    assert page.locator(".empty h3").inner_text() == "Aucun bail pour l’instant"
    assert page.locator(".alert").count() >= 3
    shot(page, "01-premier-lancement")

    # Bailleur + signature
    page.click("#nav [data-view=bailleur]")
    page.fill("#bailleurForm [name=nom]", "SCI Les Tilleuls")
    page.fill("#bailleurForm [name=adresse]", "12 rue des Lilas\n75011 Paris")
    page.fill("#bailleurForm [name=email]", "proprio@test.fr")
    page.fill("#bailleurForm [name=ville]", "Paris")
    page.click("#bailleurForm [type=submit]")
    page.wait_for_selector("#toast.show.ok")
    from PIL import Image, ImageDraw
    sig = tmp_path / "sig.png"
    im = Image.new("RGB", (600, 200), "white")
    ImageDraw.Draw(im).line([(20, 150), (120, 40), (220, 160), (320, 60), (420, 150), (580, 50)], fill=(20, 22, 70), width=8)
    im.save(sig)
    page.set_input_files("#sigFile", str(sig))
    page.wait_for_selector("#sigBox img")
    shot(page, "02-bailleur")

    # Nouveau bail + aperçu
    page.click("#nav [data-view=baux]")
    page.click("#newBail")
    page.fill("#bailForm [name=loc_prenom]", "Hélène")
    page.fill("#bailForm [name=loc_nom]", "Martin")
    page.fill("#bailForm [name=loc_email]", "helene@test.fr")
    page.fill("#bailForm [name=logement_adresse]", "3 av Jean Jaurès\n69007 Lyon")
    page.fill("#bailForm [name=loyer]", "850,50")
    page.fill("#bailForm [name=charges]", "70")
    page.select_option("#bailForm [name=mode_paiement]", "virement")
    page.check("#bailForm [name=accord_email]")
    page.wait_for_selector("#bailPreview img")
    expect(page.locator("#bailWords")).to_contain_text("neuf cent vingt euros")
    assert page.locator("#bailTotal").inner_text().replace(" ", " ") == "920,50 €"
    # envoi automatique : avertissement obligatoire
    page.click("#bailForm .auto-box .switch")
    page.wait_for_selector("#askModal:not([hidden])")
    assert "atteste que le loyer a été payé" in page.locator("#askBody").inner_text()
    shot(page, "03-avertissement-auto")
    page.click("#askYes")
    page.click("#bailForm [type=submit]")
    page.wait_for_selector("#toast.show.ok")
    shot(page, "04-bail")
    bail = store.load_baux()[0]
    assert bail.auto and bail.accord_email and bail.jour_envoi == 10 and bail.loyer == 85050

    # Messagerie de test (serveur SMTP local, sans TLS : réglage direct)
    b = store.Bailleur.load()
    b.mail_provider, b.smtp_host, b.smtp_port, b.smtp_security, b.smtp_user = "custom", "127.0.0.1", port, "none", "bailleur@test.fr"
    b.save()
    secrets.set_smtp_password("bailleur@test.fr", "127.0.0.1", "secret")

    # Tableau de bord : aperçu + envoi
    page.click("#nav [data-view=dash]")
    page.reload(); page.wait_for_selector(".bcard")
    assert "Envoi auto le 10" in page.locator(".bcard .badge").inner_text()
    shot(page, "05-tableau-de-bord")
    page.click(".bcard [data-act=preview]")
    page.wait_for_selector("#pmImg[src^='data:image/png']")
    shot(page, "06-apercu")
    page.click("#pmSend")
    page.wait_for_selector("#askModal:not([hidden])")
    page.click("#askYes")
    expect(page.locator("#toast")).to_contain_text("envoyée à helene@test.fr", timeout=15000)
    assert len(box.messages) == 1
    page.wait_for_selector(".bcard .badge.b-ok")
    shot(page, "07-envoyee")

    # Mois suivant : loyer non reçu
    page.click("#mNext")
    page.wait_for_selector(".bcard [data-act=suspend]")
    page.click(".bcard [data-act=suspend]")
    page.click("#askYes")
    page.wait_for_selector(".bcard .badge.b-susp")

    # Historique
    page.click("#nav [data-view=hist]")
    rows = page.locator("#histTable tbody tr")
    assert rows.count() == 2
    shot(page, "08-historique")

    # Messagerie & envoi automatique s'affichent
    page.click("#nav [data-view=mail]")
    page.select_option("#mailProvider", "gmail")
    assert "mot de passe d’application" in page.locator("#mailHelp").inner_text().lower()
    shot(page, "09-messagerie")
    page.click("#nav [data-view=auto]")
    assert page.locator("#autoList .li").count() == 1
    shot(page, "10-envoi-auto")
    assert errors == []
