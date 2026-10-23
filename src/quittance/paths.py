"""Emplacements des données.

- Données de l'appli : %APPDATA%\\QuittanceExpress (Windows) ou ~/.quittance-express
- Quittances archivées : Documents\\Quittances\\AAAA\\
La variable d'environnement QUITTANCE_HOME remplace les deux (tests, mode portable).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "QuittanceExpress"


def _override() -> Path | None:
    v = os.environ.get("QUITTANCE_HOME")
    return Path(v) if v else None


def data_dir() -> Path:
    o = _override()
    if o:
        p = o / "data"
    elif sys.platform == "win32":
        p = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / APP_NAME
    else:
        p = Path.home() / ".quittance-express"
    p.mkdir(parents=True, exist_ok=True)
    return p


def documents_dir() -> Path:
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
            # CSIDL_PERSONAL = 5 (Documents, y compris s'il est redirigé vers OneDrive)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0:
                return Path(buf.value)
        except Exception:
            pass
    return Path.home() / "Documents"


def archive_dir() -> Path:
    o = _override()
    p = (o / "Quittances") if o else (documents_dir() / "Quittances")
    p.mkdir(parents=True, exist_ok=True)
    return p


def logs_dir() -> Path:
    p = data_dir() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def resource(*parts: str) -> Path:
    """Fichier embarqué (fonctionne aussi dans l'exécutable PyInstaller)."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    p = base / "quittance" / Path(*parts)
    if p.exists():
        return p
    return Path(__file__).resolve().parent / Path(*parts)
