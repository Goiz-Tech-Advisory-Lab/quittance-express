"""Import / export.

- Import des modèles exportés par le module web (goiztechadvisory.fr/quittance) : {"templates": {nom: {...}}}
- Sauvegarde complète de l'appli (bailleur sans secrets + baux) : {"app": "quittance-express", ...}
"""
from __future__ import annotations

import base64
import re
from dataclasses import asdict

from . import service, signature, store
from .words import parse_amount

_DATAURL = re.compile(r"^data:image/(png|jpeg|webp);base64,(.+)$", re.S)


def _amount(v) -> int:
    try:
        return parse_amount(v)
    except (ValueError, OverflowError):
        return 0


def import_json(data: dict) -> dict:
    if not isinstance(data, dict):
        raise service.ServiceError("Fichier invalide.")
    if data.get("app") == "quittance-express":
        return _import_backup(data)
    tpls = data.get("templates")
    if not isinstance(tpls, dict) or not tpls:
        raise service.ServiceError("Aucun modèle trouvé dans ce fichier.")
    bailleur = store.Bailleur.load()
    mois = service.current_month()
    added = 0
    for name, t in tpls.items():
        if not isinstance(t, dict):
            continue
        s = lambda k: str(t.get(k) or "").strip()[:400]
        if not bailleur.nom and s("bailleurNom"):
            bailleur.nom = s("bailleurNom")
            bailleur.adresse = s("bailleurAdresse")
        if not bailleur.email and service.valid_email(s("bailleurEmail")):
            bailleur.email = s("bailleurEmail")
        if not bailleur.ville and s("faitA"):
            bailleur.ville = s("faitA")
        sig = _DATAURL.match(str(t.get("signature") or "")[:15_000_000])
        if sig and not store.has_signature():
            try:
                store.signature_path().write_bytes(signature.process(base64.b64decode(sig.group(2)), transparent=False))
            except Exception:
                pass
        email = s("locEmail")
        store.upsert_bail(service.bail_from({
            "loc_prenom": s("locPrenom"), "loc_nom": s("locNom") or str(name)[:200], "loc_email": email if service.valid_email(email) else "",
            "logement_adresse": s("logementAdresse"), "loyer": _amount(t.get("loyer")) / 100, "charges": _amount(t.get("charges")) / 100,
            "mode_paiement": s("modePaiement"), "debut": mois}))
        added += 1
    bailleur.save()
    return {"baux": added}


def export_backup() -> dict:
    b = asdict(store.Bailleur.load())
    sig = store.signature_path()
    return {"app": "quittance-express", "version": 1, "bailleur": b,
            "baux": [asdict(x) for x in store.load_baux()],
            "signature": ("data:image/png;base64," + base64.b64encode(sig.read_bytes()).decode()) if sig.exists() else ""}


def _import_backup(data: dict) -> dict:
    """Restauration d'une sauvegarde : chaque valeur est revalidée (le fichier peut avoir été modifié)."""
    from . import mailer

    raw = data.get("bailleur") if isinstance(data.get("bailleur"), dict) else {}
    b = store.Bailleur.load()
    txt = lambda k, n=400: str(raw.get(k) or "").strip()[:n]
    for k in ("nom", "adresse", "ville"):
        if k in raw:
            setattr(b, k, txt(k))
    for k in ("email", "from_email"):
        if k in raw:
            v = txt(k, 254)
            if not v or service.valid_email(v):  # adresse invalide : on garde la valeur actuelle
                setattr(b, k, v)
    if "copie_bailleur" in raw:
        b.copie_bailleur = raw["copie_bailleur"] is not False
    if raw.get("mail_provider") in mailer.PRESETS:
        b.mail_provider = raw["mail_provider"]
        b.smtp_host = txt("smtp_host", 200)
        b.smtp_user = txt("smtp_user", 200)
        b.smtp_security = raw.get("smtp_security") if raw.get("smtp_security") in ("ssl", "starttls") else "ssl"
        try:
            b.smtp_port = min(max(int(raw.get("smtp_port") or 0), 0), 65535)
        except (TypeError, ValueError):
            b.smtp_port = 0
        b.ms_client_id = txt("ms_client_id", 64)
    b.save()
    n = 0
    for item in data.get("baux") or []:
        if not isinstance(item, dict):
            continue
        bid = str(item.get("id") or "")
        base = store.Bail(id=bid) if store.ID_RE.match(bid) else store.Bail()
        try:
            store.upsert_bail(service.bail_from(item, base))
            n += 1
        except service.ServiceError:
            continue
    sig = _DATAURL.match(str(data.get("signature") or ""))
    if sig:
        try:
            store.signature_path().write_bytes(signature.process(base64.b64decode(sig.group(2)), transparent=False))
        except Exception:  # noqa: BLE001 — image invalide : ignorée
            pass
    return {"baux": n}
