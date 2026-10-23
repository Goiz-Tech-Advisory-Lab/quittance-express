"""Serveur HTTP local (127.0.0.1 uniquement) qui sert l'interface et l'API JSON.

Sécurité : chaque appel d'API exige le jeton aléatoire de la session (en-tête X-Token)
et un en-tête Host local — un site web ouvert dans le navigateur ne peut donc pas piloter l'appli.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import mimetypes
import os
import secrets as pysecrets
import subprocess
import sys
import threading
from dataclasses import asdict
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from . import __version__, importer, mailer, paths, pdfgen, scheduler, secrets, service, signature, store, winsched
from .words import amount_words, fmt_eur

log = logging.getLogger(__name__)
MAX_BODY = 20 * 1024 * 1024


def open_path(p: Path) -> None:
    if os.environ.get("QUITTANCE_NO_OPEN"):
        return
    if sys.platform == "win32":
        os.startfile(str(p))  # noqa: S606 — chemin contrôlé (dossier d'archives)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(p)])
    else:
        subprocess.Popen(["xdg-open", str(p)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def preview_png(pdf: bytes, scale: float = 1.6) -> str:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(pdf)
    try:
        img = doc[0].render(scale=scale).to_pil()
    finally:
        doc.close()
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


class Api:
    """Routes POST /api/<nom> → méthode <nom> (tirets → soulignés)."""

    # ----- état
    def state(self, _):
        b = store.Bailleur.load()
        sig = store.signature_path()
        mois = service.current_month()
        baux = []
        for x in store.load_baux():
            d = x.to_dict()
            d["statut_mois"] = store.month_status(x.id, mois)
            d["jour_envoi_effectif"] = scheduler.send_day(x, date.today().year, date.today().month)
            baux.append(d)
        hist = list(reversed(store.load_history()))[:300]
        return {
            "version": __version__, "mois": mois, "bailleur": asdict(b),
            "has_password": bool(b.smtp_user and b.smtp_host and secrets.get_smtp_password(b.smtp_user, b.smtp_host)),
            "mail_ok": mailer.is_configured(b), "presets": mailer.PRESETS,
            "ms_default_client": bool(mailer.DEFAULT_MS_CLIENT_ID),
            "signature": ("data:image/png;base64," + base64.b64encode(sig.read_bytes()).decode()) if sig.exists() else "",
            "baux": baux, "historique": hist, "manques": scheduler.missed(), "bloques": scheduler.blocked(),
            "planif": winsched.status(), "archive_dir": str(paths.archive_dir()),
        }

    def mois_status(self, d):
        mois = service.check_month(d.get("mois") or service.current_month())
        return {"mois": mois, "statuts": {b.id: store.month_status(b.id, mois) for b in store.load_baux()}}

    # ----- bailleur & signature
    def bailleur(self, d):
        b = store.Bailleur.load()
        for k in ("nom", "adresse", "email", "ville"):
            if k in d:
                setattr(b, k, str(d[k] or "").strip()[:400])
        if "copie_bailleur" in d:
            b.copie_bailleur = bool(d["copie_bailleur"])
        if b.email and not service.valid_email(b.email):
            raise service.ServiceError("Email du bailleur invalide.")
        b.save()
        return {"bailleur": asdict(b)}

    def signature(self, d):
        raw = str(d.get("data") or "")
        if "," in raw:
            raw = raw.split(",", 1)[1]
        try:
            png = signature.process(base64.b64decode(raw), transparent=bool(d.get("transparent", True)))
        except Exception:
            raise service.ServiceError("Image illisible : utilisez un PNG ou un JPG.")
        store.signature_path().write_bytes(png)
        return {"signature": "data:image/png;base64," + base64.b64encode(png).decode()}

    def signature_delete(self, _):
        store.signature_path().unlink(missing_ok=True)
        return {"signature": ""}

    # ----- baux
    def bail_save(self, d):
        base = store.get_bail(d["id"]) if d.get("id") else None
        if d.get("id") and not base:
            raise service.ServiceError("Bail introuvable.")
        bail = service.bail_from(d, base)
        store.upsert_bail(bail)
        return {"bail": bail.to_dict()}

    def bail_delete(self, d):
        return {"ok": store.delete_bail(str(d.get("id")))}

    # ----- quittances
    def preview(self, d):
        mois = service.check_month(d.get("mois") or service.current_month())
        bailleur = store.Bailleur.load()
        base = store.get_bail(d["id"]) if d.get("id") else None
        raw = {k: v for k, v in (d.get("bail") or {}).items() if k not in ("auto", "accord_email", "loc_email", "id")}
        bail = service.bail_from(raw, base or store.Bail())
        data = service.build_data(bailleur, bail, mois)
        pdf = pdfgen.render(data)
        return {"image": preview_png(pdf), "manquants": service.validate(bailleur, bail),
                "lettres": amount_words(bail.total), "total": fmt_eur(bail.total)}

    def generate(self, d):
        p = service.generate(str(d.get("id")), service.check_month(d.get("mois")))
        open_path(p)
        return {"fichier": str(p)}

    def send(self, d):
        e = service.send(str(d.get("id")), service.check_month(d.get("mois")), "manuel", bool(d.get("force")))
        return {"entree": e}

    def suspend(self, d):
        return {"entree": service.suspend(str(d.get("id")), service.check_month(d.get("mois")))}

    def unsuspend(self, d):
        return {"ok": service.unsuspend(str(d.get("id")), service.check_month(d.get("mois")))}

    def history_delete(self, d):
        return {"ok": store.remove_history(str(d.get("id")))}

    def open(self, d):
        if d.get("what") == "archive":
            open_path(paths.archive_dir())
            return {"ok": True}
        p = Path(str(d.get("path") or "")).resolve()
        root = paths.archive_dir().resolve()
        if root not in p.parents or p.suffix.lower() != ".pdf" or not p.exists():
            raise service.ServiceError("Fichier introuvable.")
        open_path(p)
        return {"ok": True}

    # ----- messagerie
    def mail_config(self, d):
        b = store.Bailleur.load()
        prov = str(d.get("provider") or "")
        if prov not in mailer.PRESETS:
            raise service.ServiceError("Messagerie inconnue.")
        old_user, old_host = b.smtp_user, b.smtp_host
        b.mail_provider = prov
        if prov == "outlook":
            if "ms_client_id" in d:
                b.ms_client_id = str(d["ms_client_id"] or "").strip()[:64]
        else:
            preset = mailer.PRESETS[prov]
            b.smtp_host = (str(d.get("host") or "").strip() if prov == "custom" else preset["host"])[:200]
            try:
                b.smtp_port = int(d.get("port") or preset["port"]) if prov == "custom" else preset["port"]
            except ValueError:
                raise service.ServiceError("Port invalide.")
            b.smtp_security = (d.get("security") if prov == "custom" else preset["security"]) or "ssl"
            if b.smtp_security not in ("ssl", "starttls"):
                raise service.ServiceError("Sécurité invalide.")
            b.smtp_user = str(d.get("user") or "").strip()[:200]
            b.from_email = str(d.get("from_email") or "").strip()[:200]
            if not b.smtp_host or not b.smtp_user:
                raise service.ServiceError("Serveur et identifiant requis.")
            if b.from_email and not service.valid_email(b.from_email):
                raise service.ServiceError("Adresse d’expédition invalide.")
            pwd = d.get("password")
            if pwd:
                secrets.set_smtp_password(b.smtp_user, b.smtp_host, str(pwd))
            if (old_user, old_host) != (b.smtp_user, b.smtp_host) and old_user and old_host and not pwd:
                pass  # l'ancien mot de passe reste lié à l'ancien compte ; il faudra le ressaisir
        b.save()
        return {"mail_ok": mailer.is_configured(b)}

    def mail_test(self, d):
        b = store.Bailleur.load()
        to = str(d.get("to") or b.email or mailer.sender_address(b))
        service.send_test(b, to)
        return {"to": to}

    # Connexion Microsoft en arrière-plan : l'interface interroge ms-status et peut relancer à tout moment.
    _ms = {"state": "idle", "error": "", "account": "", "gen": None}

    def ms_login(self, _):
        b = store.Bailleur.load()
        if not mailer.ms_client_id(b):
            raise service.ServiceError("Identifiant d’application Microsoft manquant.")
        gen = object()
        Api._ms.update(state="pending", error="", account="", gen=gen)

        def run():
            try:
                account = mailer.ms_login(b)
                if Api._ms["gen"] is gen:
                    cur = store.Bailleur.load()
                    cur.ms_account, cur.mail_provider = account, "outlook"
                    cur.save()
                    Api._ms.update(state="ok", account=account)
            except Exception as e:  # noqa: BLE001
                log.warning("Connexion Microsoft : %s", e)
                if Api._ms["gen"] is gen:
                    Api._ms.update(state="error", error=str(e))

        threading.Thread(target=run, daemon=True).start()
        return {"state": "pending"}

    def ms_status(self, _):
        return {k: Api._ms[k] for k in ("state", "error", "account")}

    def ms_logout(self, _):
        b = store.Bailleur.load()
        mailer.ms_logout(b)
        b.ms_account = ""
        b.save()
        return {"ok": True}

    # ----- planification
    def schedule_install(self, d):
        hour = int(d.get("hour", 9))
        try:
            winsched.install(min(max(hour, 0), 23), 0)
        except RuntimeError as e:
            raise service.ServiceError(str(e))
        return {"planif": winsched.status()}

    def schedule_uninstall(self, _):
        winsched.uninstall()
        return {"planif": winsched.status()}

    def run_due(self, _):
        return scheduler.run_due()

    # ----- import / export
    def import_data(self, d):
        try:
            data = json.loads(d.get("text") or "")
        except ValueError:
            raise service.ServiceError("Fichier JSON invalide.")
        return importer.import_json(data)

    def export_data(self, _):
        p = paths.archive_dir() / f"sauvegarde-quittance-express-{date.today():%Y%m%d}.json"
        p.write_text(json.dumps(importer.export_backup(), ensure_ascii=False, indent=2), encoding="utf-8")
        open_path(paths.archive_dir())
        return {"fichier": str(p)}


class Handler(BaseHTTPRequestHandler):
    server_version = "QuittanceExpress"
    api = Api()

    def log_message(self, fmt, *args):
        log.debug("%s - %s", self.address_string(), fmt % args)

    def _host_ok(self) -> bool:
        port = self.server.server_address[1]
        return self.headers.get("Host", "") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
                         "connect-src 'self'; form-action 'none'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionError):  # la page a abandonné la requête (ex. aperçu remplacé)
            pass

    def _json(self, code: int, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, b"forbidden", "text/plain")
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            path = "/index.html"
        name = path.lstrip("/")
        if "/" in name or name.startswith(".") or not name:
            return self._send(404, b"not found", "text/plain")
        f = paths.resource("ui", name)
        if not f.is_file():
            return self._send(404, b"not found", "text/plain")
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        self._send(200, f.read_bytes(), ctype)

    def do_POST(self):
        if not self._host_ok() or not pysecrets.compare_digest(self.headers.get("X-Token", ""), self.server.token):
            return self._json(403, {"ok": False, "error": "Accès refusé."})
        path = urlparse(self.path).path
        if not path.startswith("/api/"):
            return self._json(404, {"ok": False, "error": "Inconnu."})
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            return self._json(413, {"ok": False, "error": "Données trop volumineuses."})
        try:
            data = json.loads(self.rfile.read(n) or b"{}") if n else {}
        except ValueError:
            return self._json(400, {"ok": False, "error": "Requête invalide."})
        fn = getattr(self.api, path[5:].replace("-", "_"), None)
        if not fn or path[5:].startswith("_"):
            return self._json(404, {"ok": False, "error": "Inconnu."})
        try:
            res = fn(data if isinstance(data, dict) else {})
            return self._json(200, {"ok": True, **(res or {})})
        except (service.ServiceError, store.StoreError) as e:
            return self._json(400, {"ok": False, "error": str(e)})
        except (BrokenPipeError, ConnectionError):
            return None
        except Exception as e:  # noqa: BLE001
            log.exception("Erreur API %s", path)
            return self._json(500, {"ok": False, "error": f"Erreur inattendue : {e}"})


def start(port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.token = pysecrets.token_urlsafe(24)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}/?t={httpd.token}"
    return httpd, url
