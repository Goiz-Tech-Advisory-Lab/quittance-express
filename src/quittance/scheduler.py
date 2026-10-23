"""Envoi automatique : appelé par la tâche planifiée Windows (`QuittanceExpress.exe --run-due`).

Règles (garde-fous) :
- uniquement les baux actifs avec l'envoi automatique activé (désactivé par défaut) et l'accord du locataire ;
- uniquement le mois en cours, à partir du jour d'envoi (29-31 → dernier jour du mois) ;
- jamais deux envois pour le même bail et le même mois ; un mois « loyer non reçu » n'est jamais envoyé ;
- tant qu'un mois précédent est « loyer non reçu », les mois suivants ne partent pas automatiquement
  (une quittance récente laisse présumer le paiement des loyers antérieurs) ;
- les mois passés manqués ne sont pas rattrapés automatiquement (signalés dans le tableau de bord) ;
- une erreur sur un bail n'empêche pas les autres envois.
"""
from __future__ import annotations

import calendar
import logging
from datetime import date

from . import mailer, service, store

log = logging.getLogger(__name__)


def send_day(bail: store.Bail, year: int, month: int) -> int:
    try:
        d = int(bail.jour_envoi or store.DEFAULT_SEND_DAY)
    except (TypeError, ValueError):
        d = store.DEFAULT_SEND_DAY
    return min(max(d, 1), calendar.monthrange(year, month)[1])


def _eligible(b: store.Bail) -> bool:
    return b.actif and b.auto and b.accord_email


def due(today: date | None = None) -> list[tuple[store.Bail, str]]:
    t = today or date.today()
    mois = service.current_month(t)
    out = []
    for b in store.load_baux():
        if not _eligible(b):
            continue
        if b.debut and b.debut > mois:
            continue
        if t.day < send_day(b, t.year, t.month):
            continue
        if store.month_status(b.id, mois):
            continue
        if store.suspended_before(b.id, mois):
            continue
        out.append((b, mois))
    return out


def blocked(today: date | None = None) -> list[dict]:
    """Baux en automatique bloqués par un mois antérieur « loyer non reçu »."""
    mois = service.current_month(today)
    out = []
    for b in store.load_baux():
        if _eligible(b):
            s = store.suspended_before(b.id, mois)
            if s:
                out.append({"bail_id": b.id, "locataire": b.locataire, "mois": s})
    return out


def run_due(today: date | None = None) -> dict:
    """Envoie les quittances dues. Retourne un résumé {envoyees, echecs, ignore}."""
    summary = {"envoyees": [], "echecs": [], "ignore": None}
    with store.file_lock("auto", blocking=False) as held:
        if not held:
            summary["ignore"] = "Un envoi automatique est déjà en cours."
            log.info(summary["ignore"])
            return summary
        try:
            items = due(today)
        except store.StoreError as e:
            summary["ignore"] = str(e)
            log.error("Envoi automatique annulé : %s", e)
            return summary
        for b in blocked(today):
            log.warning("Envoi automatique bloqué pour %s : loyer non reçu (%s).", b["locataire"], ", ".join(b["mois"]))
        if not items:
            log.info("Envoi automatique : rien à envoyer.")
            return summary
        if not mailer.is_configured(store.Bailleur.load()):
            summary["ignore"] = "Messagerie non configurée."
            log.warning("Envoi automatique : %d quittance(s) due(s) mais messagerie non configurée.", len(items))
            return summary
        for bail, mois in items:
            try:
                service.send(bail.id, mois, origine="auto")
                summary["envoyees"].append(f"{bail.locataire} ({mois})")
            except service.ServiceError as e:
                summary["echecs"].append(f"{bail.locataire} ({mois}) : {e}")
            except Exception as e:  # noqa: BLE001 — un bail en erreur ne bloque pas les suivants
                log.exception("Erreur inattendue pour %s %s", bail.locataire, mois)
                summary["echecs"].append(f"{bail.locataire} ({mois}) : erreur inattendue ({e})")
    return summary


def missed(today: date | None = None, months_back: int = 3) -> list[dict]:
    """Mois passés (auto activé) restés sans quittance ni suspension — à traiter à la main."""
    t = today or date.today()
    out = []
    for b in store.load_baux():
        if not _eligible(b):
            continue
        y, m = t.year, t.month
        for _ in range(months_back):
            m -= 1
            if m == 0:
                y, m = y - 1, 12
            mois = f"{y}-{m:02d}"
            if b.debut and b.debut > mois:
                continue
            if not store.month_status(b.id, mois):
                out.append({"bail_id": b.id, "locataire": b.locataire, "mois": mois})
    return out
