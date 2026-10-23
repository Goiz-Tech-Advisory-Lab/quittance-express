"""Opérations métier : construire, archiver et envoyer une quittance."""
from __future__ import annotations

import calendar
import logging
import re
from datetime import date
from pathlib import Path

from . import mailer, paths, pdfgen, store
from .store import Bail, Bailleur
from .words import fmt_eur, parse_amount

log = logging.getLogger(__name__)
EMAIL_RE = re.compile(r"^[^\s@,;<>\"']+@[^\s@,;<>\"']+\.[^\s@,;<>\"']+$")
MOIS_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
MODES = ("", "virement", "prélèvement", "chèque", "espèces")


class ServiceError(Exception):
    pass


def current_month(today: date | None = None) -> str:
    t = today or date.today()
    return f"{t.year}-{t.month:02d}"


def check_month(mois) -> str:
    if not isinstance(mois, str) or not MOIS_RE.match(mois):
        raise ServiceError("Mois invalide (format attendu AAAA-MM).")
    return mois


def valid_email(s) -> bool:
    return isinstance(s, str) and len(s) <= 254 and bool(EMAIL_RE.match(s))


def bail_from(d: dict, base: Bail | None = None) -> Bail:
    """Construit/valide un bail à partir de données non fiables (interface, import)."""
    if not isinstance(d, dict):
        raise ServiceError("Données de bail invalides.")
    b = base or Bail()

    def txt(k, n=200):
        v = d.get(k)
        return (str(v) if v is not None else "").strip()[:n]

    for k in ("loc_prenom", "loc_nom", "loc_email", "note"):
        if k in d:
            setattr(b, k, txt(k))
    if "logement_adresse" in d:
        b.logement_adresse = txt("logement_adresse", 400)
    if "mode_paiement" in d:
        m = txt("mode_paiement")
        b.mode_paiement = m if m in MODES else ""
    if "type_charges" in d:
        b.type_charges = "forfait" if d.get("type_charges") == "forfait" else "provision"
    for k in ("loyer", "charges"):
        if k in d:
            try:
                setattr(b, k, parse_amount(d[k]) if not isinstance(d[k], int) or isinstance(d[k], bool) else _cents_int(d[k]))
            except (ValueError, OverflowError, TypeError):
                raise ServiceError("Montant invalide (nombre positif attendu).")
    for k, lo, hi in (("jour_paiement", 0, 31), ("jour_envoi", 1, 31)):
        if k in d:
            try:
                v = int(d[k] or 0)
            except (TypeError, ValueError):
                raise ServiceError("Jour invalide.")
            setattr(b, k, min(max(v, lo), hi))
    if "debut" in d:
        b.debut = check_month(d["debut"]) if d["debut"] else ""
    for k in ("actif", "auto", "accord_email"):
        if k in d:
            setattr(b, k, d[k] is True)
    if b.loc_email and not valid_email(b.loc_email):
        raise ServiceError("Email du locataire invalide.")
    if b.auto and not (b.loc_email and b.accord_email):
        raise ServiceError("L’envoi automatique nécessite l’email du locataire et son accord pour l’envoi par email.")
    if not b.debut:
        b.debut = current_month()
    return b


def _cents_int(v: int) -> int:
    """Entier venant d'une sauvegarde : déjà en centimes."""
    if v < 0 or v > 1_000_000_000:
        raise ValueError
    return v


def build_data(bailleur: Bailleur, bail: Bail, mois: str, emission: date | None = None,
               with_payment_date: bool = True) -> pdfgen.QuittanceData:
    du, au = pdfgen.month_bounds(check_month(mois))
    paiement = None
    if with_payment_date and bail.jour_paiement:
        paiement = date(du.year, du.month, min(int(bail.jour_paiement), calendar.monthrange(du.year, du.month)[1]))
    sig = store.signature_path().read_bytes() if store.has_signature() else None
    return pdfgen.QuittanceData(
        bailleur_nom=bailleur.nom, bailleur_adresse=bailleur.adresse,
        locataire=bail.locataire, logement_adresse=bail.logement_adresse,
        loyer=bail.loyer, charges=bail.charges, du=du, au=au,
        date_emission=emission or date.today(), ville=bailleur.ville,
        date_paiement=paiement, mode_paiement=bail.mode_paiement, signature_png=sig,
        charges_forfait=bail.type_charges == "forfait",
    )


def validate(bailleur: Bailleur, bail: Bail) -> list[str]:
    missing = []
    if not bailleur.nom.strip():
        missing.append("nom du bailleur")
    if not bail.loc_nom.strip():
        missing.append("nom du locataire")
    if not bail.logement_adresse.strip():
        missing.append("adresse du logement")
    if bail.loyer <= 0:
        missing.append("loyer")
    return missing


def render(bail_id: str, mois: str, emission: date | None = None, with_payment_date: bool = True) -> tuple[bytes, str, Bail]:
    bailleur = store.Bailleur.load()
    bail = store.get_bail(bail_id)
    if not bail:
        raise ServiceError("Bail introuvable.")
    missing = validate(bailleur, bail)
    if missing:
        raise ServiceError("À compléter : " + ", ".join(missing) + ".")
    data = build_data(bailleur, bail, mois, emission, with_payment_date)
    try:
        pdf = pdfgen.render(data)
    except Exception as e:  # noqa: BLE001
        log.exception("Génération PDF impossible")
        raise ServiceError(f"Génération du PDF impossible : {e}") from e
    return pdf, pdfgen.file_name(bail.loc_nom, data.du), bail


def archive(pdf: bytes, name: str, mois: str) -> Path:
    """Enregistre sans jamais écraser une quittance existante (…-2.pdf, …-3.pdf)."""
    d = paths.archive_dir() / mois[:4]
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    n = 2
    while p.exists():
        p = d / f"{Path(name).stem}-{n}.pdf"
        n += 1
    p.write_bytes(pdf)
    return p


def generate(bail_id: str, mois: str) -> Path:
    pdf, name, bail = render(bail_id, mois)
    p = archive(pdf, name, mois)
    store.add_history({"bail_id": bail_id, "locataire": bail.locataire, "mois": mois, "statut": "generee",
                       "fichier": str(p), "origine": "manuel"})
    return p


def mail_texts(bailleur: Bailleur, bail: Bail, mois: str) -> tuple[str, str]:
    du, au = pdfgen.month_bounds(mois)
    hello = bail.loc_prenom or bail.locataire
    subject = f"Quittance de loyer – {pdfgen.period_label(du, au)}"
    body = (f"Bonjour {hello},\n\n"
            f"Veuillez trouver ci-joint votre quittance de loyer pour la période {pdfgen.period_phrase(du, au)} "
            f"(montant réglé : {fmt_eur(bail.total).replace(chr(160), ' ')}).\n\n"
            f"Cordialement,\n{bailleur.nom}\n")
    return subject, body


def send(bail_id: str, mois: str, origine: str = "manuel", force: bool = False) -> dict:
    """Génère, archive et envoie.

    Idempotent : un seul envoi par bail et par mois (sauf renvoi manuel explicite, force=True).
    Le verrou « envoi » couvre vérification + envoi + enregistrement : l'interface et la tâche
    planifiée ne peuvent pas envoyer la même quittance en même temps.
    """
    check_month(mois)
    with store.file_lock("envoi", timeout=120):
        status = store.month_status(bail_id, mois)
        if status and not (force and origine == "manuel" and status["statut"] == "envoyee"):
            if status["statut"] == "suspendue":
                raise ServiceError("Ce mois est marqué « loyer non reçu » : réactivez-le avant d’envoyer.")
            raise ServiceError(f"Quittance déjà envoyée pour ce mois (le {status['date'][:10]}).")
        bailleur = store.Bailleur.load()
        bail = store.get_bail(bail_id)
        if not bail:
            raise ServiceError("Bail introuvable.")
        if not valid_email(bail.loc_email):
            raise ServiceError("Email du locataire manquant ou invalide.")
        if not bail.accord_email:
            raise ServiceError("Le locataire n’a pas donné son accord pour recevoir ses quittances par email "
                               "(case à cocher dans la fiche du bail).")
        if not mailer.is_configured(bailleur):
            raise ServiceError("Messagerie non configurée : ouvrez « Messagerie ».")
        # envoi automatique : on n'affirme pas une date de paiement qui n'a pas été constatée
        pdf, name, bail = render(bail_id, mois, with_payment_date=(origine == "manuel"))
        subject, body = mail_texts(bailleur, bail, mois)
        cc = bailleur.email if (bailleur.copie_bailleur and valid_email(bailleur.email)) else None
        try:
            msg = mailer.build_message(bailleur, bail.loc_email, subject, body, pdf, name, cc=cc)
            mailer.send(bailleur, msg)
        except (mailer.MailError, ValueError) as e:
            path = archive(pdf, name, mois)
            store.add_history({"bail_id": bail_id, "locataire": bail.locataire, "mois": mois, "statut": "echec",
                               "erreur": str(e), "origine": origine, "fichier": str(path)})
            log.error("Échec d'envoi %s %s : %s", bail.locataire, mois, e)
            raise ServiceError(str(e)) from e
        entry = {"bail_id": bail_id, "locataire": bail.locataire, "mois": mois, "statut": "envoyee",
                 "destinataire": bail.loc_email, "copie": cc or "", "origine": origine, "montant": bail.total}
        try:
            entry = store.add_history(entry)  # d'abord l'historique : c'est lui qui empêche un second envoi
        finally:
            try:
                path = archive(pdf, name, mois)
                entry["fichier"] = str(path)
                _set_history_file(entry.get("id"), str(path))
            except OSError:
                log.exception("Archivage du PDF impossible")
        log.info("Quittance envoyée : %s %s → %s (%s)", bail.locataire, mois, bail.loc_email, origine)
        return entry


def _set_history_file(entry_id: str | None, path: str) -> None:
    if not entry_id:
        return
    with store.file_lock():
        h = store.load_history()
        for e in h:
            if e.get("id") == entry_id:
                e["fichier"] = path
        store._write("historique.json", h)


def suspend(bail_id: str, mois: str) -> dict:
    check_month(mois)
    with store.file_lock("envoi", timeout=120):
        bail = store.get_bail(bail_id)
        if not bail:
            raise ServiceError("Bail introuvable.")
        st = store.month_status(bail_id, mois)
        if st and st["statut"] == "envoyee":
            raise ServiceError("La quittance de ce mois est déjà envoyée.")
        if st:
            return st
        return store.add_history({"bail_id": bail_id, "locataire": bail.locataire, "mois": mois, "statut": "suspendue",
                                  "origine": "manuel"})


def unsuspend(bail_id: str, mois: str) -> bool:
    with store.file_lock("envoi", timeout=120):
        st = store.month_status(bail_id, mois)
        if st and st["statut"] == "suspendue":
            return store.remove_history(st["id"])
        return False


def send_test(bailleur: Bailleur, to: str, password: str | None = None) -> None:
    if not valid_email(to):
        raise ServiceError("Adresse de test invalide.")
    try:
        msg = mailer.build_message(bailleur, to, "Quittance Express – email de test",
                                   "Bonjour,\n\nCe message confirme que Quittance Express peut envoyer des emails depuis votre boîte.\n\n"
                                   "Quittance Express\n", None, None)
        mailer.send(bailleur, msg, password)
    except (mailer.MailError, ValueError) as e:
        raise ServiceError(str(e)) from e
