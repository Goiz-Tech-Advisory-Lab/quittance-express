"""Modèle de données et persistance JSON.

Fichiers dans data_dir() :
  bailleur.json   — identité du bailleur + réglages d'envoi (sans mot de passe)
  baux.json       — liste des baux (locataire, logement, montants, envoi automatique)
  historique.json — quittances émises / mois suspendus (clé d'idempotence : bail_id + mois)
  signature.png   — signature du bailleur (fond transparent)

Robustesse :
- écritures atomiques (fichier temporaire + fsync + remplacement, avec nouvelles tentatives sous Windows) ;
- verrou inter-processus : l'interface et la tâche planifiée peuvent tourner en même temps ;
- une lecture impossible n'est JAMAIS traitée comme un fichier vide (sinon l'historique serait perdu
  et des quittances renvoyées) : l'erreur remonte et rien n'est envoyé.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path
from typing import Any

from . import paths

DEFAULT_SEND_DAY = 10
ID_RE = re.compile(r"^[0-9a-f]{12}$")

_rlock = threading.RLock()
_held = threading.local()


class StoreError(Exception):
    """Données illisibles ou occupées : message présentable à l'utilisateur."""


# ---------------------------------------------------------------- verrou inter-processus

def _os_lock(f, blocking: bool) -> bool:
    try:
        if sys.platform == "win32":
            import msvcrt

            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _os_unlock(f) -> None:
    try:
        if sys.platform == "win32":
            import msvcrt

            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


@contextlib.contextmanager
def file_lock(name: str = "donnees", timeout: float = 30.0, blocking: bool = True):
    """Verrou exclusif entre processus (réentrant dans un même thread).

    Rend True si acquis ; avec blocking=False, rend False sans attendre si un autre processus le détient.
    Le système libère le verrou si le processus s'arrête brutalement.
    """
    depth = getattr(_held, name, 0)
    if depth:
        setattr(_held, name, depth + 1)
        try:
            yield True
        finally:
            setattr(_held, name, getattr(_held, name) - 1)
        return
    with _rlock if blocking else contextlib.nullcontext():
        f = open(paths.data_dir() / f".{name}.lock", "a+b")
        acquired = False
        try:
            start = time.monotonic()
            while not (acquired := _os_lock(f, blocking)):
                if not blocking:
                    break
                if time.monotonic() - start > timeout:
                    raise StoreError("Les données sont occupées par une autre opération. Réessayez dans un instant.")
                time.sleep(0.05)
            if acquired:
                setattr(_held, name, 1)
            yield acquired
        finally:
            if acquired:
                setattr(_held, name, 0)
                _os_unlock(f)
            f.close()


# ---------------------------------------------------------------- lecture / écriture

def _read(name: str, default):
    p = paths.data_dir() / name
    last: Exception | None = None
    for _ in range(6):
        if not p.exists():
            return default
        try:
            raw = p.read_text(encoding="utf-8")
        except FileNotFoundError:
            return default
        except OSError as e:  # fichier momentanément verrouillé (antivirus, OneDrive…) : on réessaie
            last = e
            time.sleep(0.15)
            continue
        try:
            return json.loads(raw)
        except ValueError as e:
            raise StoreError(f"Le fichier de données « {name} » est illisible. Restaurez une sauvegarde "
                             f"(Bailleur & signature → Sauvegarde) ou supprimez-le.") from e
    raise StoreError(f"Le fichier « {name} » est inaccessible pour le moment ({last}). Réessayez.")


def _write(name: str, data) -> None:
    d = paths.data_dir()
    fd, tmp = tempfile.mkstemp(dir=d, prefix="." + name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(20):
            try:
                os.replace(tmp, d / name)
                break
            except PermissionError:  # cible ouverte par un autre programme sous Windows
                if attempt == 19:
                    raise StoreError(f"Impossible d’enregistrer « {name} » (fichier occupé).")
                time.sleep(0.1)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _clean(cls, raw: dict) -> dict:
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in (raw or {}).items() if k in names}


@dataclass
class Bailleur:
    nom: str = ""
    adresse: str = ""
    email: str = ""          # copie de chaque envoi + adresse de réponse
    ville: str = ""          # « Fait à … »
    copie_bailleur: bool = True
    # réglages de messagerie (aucun secret ici : le mot de passe est dans le Gestionnaire d'identifiants)
    mail_provider: str = ""  # gmail | ovh | orange | free | sfr | laposte | yahoo | icloud | custom | outlook
    smtp_host: str = ""
    smtp_port: int = 0
    smtp_security: str = "ssl"  # ssl | starttls
    smtp_user: str = ""
    from_email: str = ""
    ms_client_id: str = ""   # Outlook.com : ID de l'application Microsoft (non secret)
    ms_account: str = ""     # compte Microsoft connecté (affichage)

    @classmethod
    def load(cls) -> "Bailleur":
        return cls(**_clean(cls, _read("bailleur.json", {})))

    def save(self) -> None:
        with file_lock():
            _write("bailleur.json", asdict(self))


@dataclass
class Bail:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    loc_prenom: str = ""
    loc_nom: str = ""
    loc_email: str = ""
    logement_adresse: str = ""
    loyer: int = 0            # centimes, hors charges
    charges: int = 0          # centimes
    type_charges: str = "provision"  # provision (régularisation annuelle) | forfait
    mode_paiement: str = ""   # virement | prélèvement | chèque | espèces | ""
    jour_paiement: int = 0    # 0 = non précisé ; sinon jour habituel du paiement
    debut: str = ""           # « AAAA-MM » : premier mois quittancé
    actif: bool = True
    accord_email: bool = False  # accord exprès du locataire pour l'envoi dématérialisé (art. 21)
    auto: bool = False        # envoi automatique (désactivé par défaut)
    jour_envoi: int = DEFAULT_SEND_DAY
    note: str = ""

    @property
    def locataire(self) -> str:
        return f"{self.loc_prenom} {self.loc_nom}".strip()

    @property
    def total(self) -> int:
        return self.loyer + self.charges

    def to_dict(self) -> dict:
        d = asdict(self)
        d["locataire"] = self.locataire
        d["total"] = self.total
        return d


def load_baux() -> list[Bail]:
    return [Bail(**_clean(Bail, b)) for b in _read("baux.json", []) if isinstance(b, dict)]


def save_baux(baux: list[Bail]) -> None:
    with file_lock():
        _write("baux.json", [asdict(b) for b in baux])


def get_bail(bail_id: str) -> Bail | None:
    return next((b for b in load_baux() if b.id == bail_id), None)


def upsert_bail(bail: Bail) -> Bail:
    with file_lock():
        baux = load_baux()
        for i, b in enumerate(baux):
            if b.id == bail.id:
                baux[i] = bail
                break
        else:
            baux.append(bail)
        save_baux(baux)
        return bail


def delete_bail(bail_id: str) -> bool:
    with file_lock():
        baux = load_baux()
        kept = [b for b in baux if b.id != bail_id]
        save_baux(kept)
        return len(kept) != len(baux)


# ---------------------------------------------------------------- historique
# statut : "envoyee" (email parti), "generee" (PDF seul), "suspendue" (loyer non reçu), "echec"

def load_history() -> list[dict[str, Any]]:
    h = _read("historique.json", [])
    return [e for e in h if isinstance(e, dict)] if isinstance(h, list) else []


def add_history(entry: dict[str, Any]) -> dict[str, Any]:
    with file_lock():
        h = load_history()
        entry = {"id": uuid.uuid4().hex[:12], "date": datetime.now().isoformat(timespec="seconds"), **entry}
        h.append(entry)
        _write("historique.json", h)
        return entry


def remove_history(entry_id: str) -> bool:
    with file_lock():
        h = load_history()
        kept = [e for e in h if e.get("id") != entry_id]
        _write("historique.json", kept)
        return len(kept) != len(h)


def month_status(bail_id: str, mois: str) -> dict[str, Any] | None:
    """Dernier état « bloquant » d'un bail pour un mois : envoyée ou suspendue."""
    for e in reversed(load_history()):
        if e.get("bail_id") == bail_id and e.get("mois") == mois and e.get("statut") in ("envoyee", "suspendue"):
            return e
    return None


def suspended_before(bail_id: str, mois: str) -> list[str]:
    """Mois antérieurs encore marqués « loyer non reçu » pour ce bail."""
    return sorted({e["mois"] for e in load_history()
                   if e.get("bail_id") == bail_id and e.get("statut") == "suspendue" and str(e.get("mois", "")) < mois})


# ---------------------------------------------------------------- signature

def signature_path() -> Path:
    return paths.data_dir() / "signature.png"


def has_signature() -> bool:
    return signature_path().exists()
