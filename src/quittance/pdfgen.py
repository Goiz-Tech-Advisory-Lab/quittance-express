"""Génération de la quittance PDF (A4) — seule source de vérité du document.

Mise en page identique au module web goiztechadvisory.fr/quittance.
"""
from __future__ import annotations

import calendar
import io
import re
import unicodedata
from dataclasses import dataclass
from datetime import date

from fpdf import FPDF

from . import paths
from .words import amount_words, fmt_eur

MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
        "septembre", "octobre", "novembre", "décembre"]

INK = (29, 33, 48)
INK_2 = (58, 63, 82)
MUTED = (122, 128, 149)
LINE = (230, 232, 239)
BOX = (223, 226, 234)
VIOLET = (108, 92, 231)
TOTAL_BG = (243, 242, 253)

PX = 210 / 794  # 1 px du modèle web en mm
PT = 0.75       # 1 px en points


@dataclass
class QuittanceData:
    bailleur_nom: str
    bailleur_adresse: str
    locataire: str
    logement_adresse: str
    loyer: int                 # centimes
    charges: int               # centimes
    du: date
    au: date
    date_emission: date
    ville: str = ""
    date_paiement: date | None = None
    mode_paiement: str = ""
    signature_png: bytes | None = None
    charges_forfait: bool = False

    @property
    def total(self) -> int:
        return self.loyer + self.charges


def month_bounds(mois: str) -> tuple[date, date]:
    y, m = (int(x) for x in mois.split("-"))
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])


def period_label(du: date, au: date) -> str:
    if du.day == 1 and du.year == au.year and du.month == au.month and au.day == calendar.monthrange(au.year, au.month)[1]:
        return f"{MOIS[du.month - 1]} {du.year}"
    return f"du {fmt_date(du)} au {fmt_date(au)}"


def period_phrase(du: date, au: date) -> str:
    """« d’octobre 2026 », « de novembre 2026 » ou « du 01/10/2026 au 15/10/2026 »."""
    p = period_label(du, au)
    if p.startswith("du "):
        return p
    return ("d’" if p[0] in "aeiouyéèâ" else "de ") + p


def fmt_date(d: date | None) -> str:
    return d.strftime("%d/%m/%Y") if d else ""


def slug(s: str) -> str:
    s = unicodedata.normalize("NFD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").upper()


def file_name(locataire_nom: str, du: date) -> str:
    return f"quittance_{slug(locataire_nom) or 'LOCATAIRE'}_{du.year}-{du.month:02d}.pdf"


def _md(s: str) -> str:
    """Neutralise les marqueurs markdown de fpdf2 dans le texte saisi."""
    return (s or "").replace("**", "* *").replace("__", "_ _").replace("--", "- -")


class _Doc(FPDF):
    def __init__(self):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.set_auto_page_break(False)
        self.set_margins(0, 0, 0)
        f = lambda n: str(paths.resource("assets", "fonts", n))
        self.add_font("Inter", "", f("Inter-Regular.ttf"))
        self.add_font("Inter", "B", f("Inter-Bold.ttf"))
        self.add_font("Inter", "I", f("Inter-Italic.ttf"))
        self.add_font("InterSB", "", f("Inter-SemiBold.ttf"))
        self.add_font("InterXB", "", f("Inter-ExtraBold.ttf"))
        self.set_creator("Quittance Express")
        self.set_lang("fr-FR")

    def font(self, size_px: float, style: str = "", family: str = "Inter", color=INK):
        self.set_font(family, style, size_px * PT)
        self.set_text_color(*color)


def render(q: QuittanceData) -> bytes:
    pdf = _Doc()
    pdf.set_title(f"Quittance de loyer – {period_label(q.du, q.au)}")
    pdf.set_author(q.bailleur_nom)
    pdf.add_page()

    L = 70 * PX
    R = 210 - 70 * PX
    W = R - L
    y = 64 * PX

    # ---- En-tête
    pdf.font(30, family="InterXB")
    pdf.set_xy(L, y)
    pdf.multi_cell(W * 0.6, 30 * PX * 1.0, "QUITTANCE\nDE LOYER")
    title_bottom = pdf.get_y()
    pdf.font(13, color=(85, 91, 112))
    pdf.set_xy(L + W * 0.5, y + 4)
    pdf.cell(W * 0.5, 5, "Période de location", align="R")
    pdf.font(17, family="Inter", style="B")
    pdf.set_xy(L + W * 0.5, y + 9.5)
    pl = period_label(q.du, q.au)
    pdf.cell(W * 0.5, 6, pl[:1].upper() + pl[1:], align="R")
    y = title_bottom + 16 * PX
    pdf.set_draw_color(*INK)
    pdf.set_line_width(2 * PX)
    pdf.line(L, y, R, y)
    y += 30 * PX

    # ---- Bailleur / Locataire
    gap = 22 * PX
    bw = (W - gap) / 2
    pad_x, pad_y = 16 * PX, 14 * PX

    def party(x: float, label: str, name: str, addr: str, sub: str | None) -> float:
        cy = y + pad_y
        pdf.font(10.5, "B", color=VIOLET)
        pdf.set_char_spacing(1.4 * PT)
        pdf.set_xy(x + pad_x, cy)
        pdf.cell(bw - 2 * pad_x, 4, label.upper())
        pdf.set_char_spacing(0)
        cy += 6
        pdf.font(14.5, "B")
        pdf.set_xy(x + pad_x, cy)
        pdf.multi_cell(bw - 2 * pad_x, 5.2, name)
        cy = pdf.get_y()
        pdf.font(13.5, color=INK_2)
        pdf.set_xy(x + pad_x, cy)
        pdf.multi_cell(bw - 2 * pad_x, 5.2, addr)
        cy = pdf.get_y()
        if sub:
            pdf.font(11, color=MUTED)
            pdf.set_xy(x + pad_x, cy + 1.5)
            pdf.cell(bw - 2 * pad_x, 4, sub)
            cy += 5.5
        return cy + pad_y

    h1 = party(L, "Bailleur", q.bailleur_nom, q.bailleur_adresse, None)
    h2 = party(L + bw + gap, "Locataire", q.locataire, q.logement_adresse, "Adresse du logement loué")
    bh = max(h1, h2) - y
    pdf.set_draw_color(*BOX)
    pdf.set_line_width(1 * PX)
    for x in (L, L + bw + gap):
        pdf.rect(x, y, bw, bh, style="D", round_corners=True, corner_radius=8 * PX)
    y += bh + 28 * PX

    # ---- Déclaration
    total = q.total
    text = (f"Je soussigné(e) **{_md(q.bailleur_nom)}**, bailleur du logement désigné ci-dessus, "
            f"déclare avoir reçu de **{_md(q.locataire)}** la somme de **{fmt_eur(total)}** "
            f"({amount_words(total)}), au titre du paiement du loyer et des charges pour la période de location "
            f"du **{fmt_date(q.du)}** au **{fmt_date(q.au)}**, et lui en donne quittance, sous réserve de tous mes droits.")
    pdf.font(13.5)
    pdf.set_xy(L, y)
    pdf.multi_cell(W, 13.5 * PX * 1.55, text, align="J", markdown=True)
    y = pdf.get_y() + 26 * PX

    # ---- Détail
    row_h = (13.5 * 1.55 + 20) * PX
    rows = [("Loyer (hors charges)", q.loyer, False),
            ("Charges (forfait)" if q.charges_forfait else "Provision pour charges", q.charges, False),
            ("Total réglé", total, True)]
    for label, cents, is_total in rows:
        if is_total:
            pdf.set_fill_color(*TOTAL_BG)
            pdf.rect(L, y, W, row_h, style="F")
            pdf.font(15, family="InterXB")
        else:
            pdf.font(13.5)
        pdf.set_xy(L + 14 * PX, y)
        pdf.cell(W / 2, row_h, label)
        pdf.set_xy(L + W / 2, y)
        pdf.cell(W / 2 - 14 * PX, row_h, fmt_eur(cents), align="R")
        y += row_h
        if not is_total:
            pdf.set_draw_color(*LINE)
            pdf.line(L, y, R, y)
    y += 16 * PX

    # ---- Paiement
    if q.date_paiement or q.mode_paiement:
        s = "Paiement reçu"
        if q.date_paiement:
            s += f" le **{fmt_date(q.date_paiement)}**"
        if q.mode_paiement:
            s += f" par {_md(q.mode_paiement)}"
        pdf.font(12.5, color=INK_2)
        pdf.set_xy(L, y)
        pdf.multi_cell(W, 5, s + ".", markdown=True)
        y = pdf.get_y()
    y += 30 * PX

    # ---- Signature
    sx = R - 290 * PX
    sw = 290 * PX
    pdf.font(13.5)
    pdf.set_xy(sx, y)
    place = (f"Fait à **{_md(q.ville)}**, le " if q.ville else "Fait le ") + f"**{fmt_date(q.date_emission)}**"
    pdf.multi_cell(sw, 5.5, place, markdown=True)
    y = pdf.get_y() + 8 * PX
    pdf.font(11, color=MUTED)
    pdf.set_xy(sx, y)
    pdf.cell(sw, 4, "Signature du bailleur")
    y += 6 * PX + 4
    box_h = 100 * PX
    if q.signature_png:
        try:
            pdf.image(io.BytesIO(q.signature_png), x=sx, y=y, h=box_h, keep_aspect_ratio=True, w=260 * PX)
        except Exception:
            pass
    y += box_h + 4 * PX
    pdf.font(13.5, "B")
    pdf.set_xy(sx, y)
    pdf.multi_cell(sw, 5.5, q.bailleur_nom)

    # ---- Pied de page
    fy = 297 - 52 * PX - 12
    pdf.set_draw_color(*LINE)
    pdf.set_line_width(1 * PX)
    pdf.line(L, fy, R, fy)
    pdf.font(10.5, color=MUTED)
    pdf.set_xy(L, fy + 12 * PX)
    pdf.multi_cell(W, 4, "Cette quittance annule tous les reçus qui auraient pu être établis précédemment en cas de paiement "
                         "partiel du montant du présent terme. Quittance délivrée en application de l’article 21 de la "
                         "loi n° 89-462 du 6 juillet 1989.")
    return bytes(pdf.output())
