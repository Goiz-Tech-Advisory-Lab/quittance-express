"""Point d'entrée : fenêtre de l'appli, ou envoi automatique sans fenêtre (--run-due)."""
from __future__ import annotations

import argparse
import logging
import sys
from logging.handlers import RotatingFileHandler

from . import __version__, paths


def setup_logging(verbose: bool = False) -> None:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    fh = RotatingFileHandler(paths.logs_dir() / "quittance.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(fh)
    if sys.stderr and not getattr(sys, "frozen", False):
        root.addHandler(logging.StreamHandler())


def selftest(log) -> int:
    """Génère un PDF + son aperçu, charge le coffre d'identifiants et les modules Microsoft (vérification d'exécutable)."""
    try:
        from datetime import date

        import keyring
        import msal  # noqa: F401
        import msal_extensions  # noqa: F401

        from . import pdfgen, server

        q = pdfgen.QuittanceData("Test", "1 rue A", "Locataire Test", "2 rue B", 85050, 7000,
                                 date(2026, 1, 1), date(2026, 1, 31), date(2026, 1, 10), "Paris")
        pdf = pdfgen.render(q)
        assert pdf[:5] == b"%PDF-"
        assert server.preview_png(pdf).startswith("data:image/png;base64,")
        log.info("Autotest OK — keyring : %s", type(keyring.get_keyring()).__name__)
        if sys.platform == "win32":
            import webview  # noqa: F401
        return 0
    except Exception:  # noqa: BLE001
        log.exception("Autotest en échec")
        return 3


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="QuittanceExpress", description="Quittances de loyer : génération, envoi, envoi automatique.")
    p.add_argument("--run-due", action="store_true", help="envoie les quittances dues puis quitte (tâche planifiée)")
    p.add_argument("--serve", action="store_true", help="serveur seul, sans fenêtre (développement / tests)")
    p.add_argument("--uninstall-task", action="store_true", help="supprime la tâche planifiée (désinstallation)")
    p.add_argument("--selftest", action="store_true", help="vérifie que tous les composants sont présents (CI)")
    p.add_argument("--port", type=int, default=0)
    args = p.parse_args(argv)
    setup_logging()
    log = logging.getLogger("quittance")

    if args.uninstall_task:
        from . import winsched

        winsched.uninstall()
        return 0

    if args.run_due:
        try:
            from . import scheduler

            s = scheduler.run_due()
            log.info("Envoi automatique terminé : %s", s)
            return 1 if s["echecs"] else 0
        except Exception:  # noqa: BLE001 — journaliser, jamais de plantage silencieux
            log.exception("Envoi automatique interrompu")
            return 2

    if args.selftest:
        return selftest(log)

    from . import server

    httpd, url = server.start(args.port)
    if args.serve:
        print(url, flush=True)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        return 0
    try:
        import webview
    except ImportError:
        import webbrowser

        webbrowser.open(url)
        print("Interface ouverte dans le navigateur :", url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        return 0
    webview.create_window("Quittance Express", url, width=1320, height=900, min_size=(980, 680),
                          background_color="#0B0D1A", text_select=True)
    webview.start(private_mode=False, storage_path=str(paths.data_dir() / "webview"))
    httpd.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
