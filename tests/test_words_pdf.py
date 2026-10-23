from datetime import date

import pypdfium2 as pdfium
import pytest

from quittance import pdfgen
from quittance.words import amount_words, fmt_eur, number_words, parse_amount


@pytest.mark.parametrize("n,expected", [
    (0, "zéro"), (1, "un"), (21, "vingt et un"), (61, "soixante et un"), (71, "soixante et onze"),
    (80, "quatre-vingts"), (81, "quatre-vingt-un"), (91, "quatre-vingt-onze"), (99, "quatre-vingt-dix-neuf"),
    (100, "cent"), (200, "deux cents"), (201, "deux cent un"), (1000, "mille"), (1200, "mille deux cents"),
    (2000, "deux mille"), (80000, "quatre-vingt mille"), (200000, "deux cent mille"),
    (1_000_000, "un million"), (2_300_000, "deux millions trois cent mille"),
])
def test_number_words(n, expected):
    assert number_words(n) == expected


@pytest.mark.parametrize("cents,expected", [
    (85020, "huit cent cinquante euros et vingt centimes"), (101, "un euro et un centime"), (1, "un centime"),
    (99, "quatre-vingt-dix-neuf centimes"), (75000, "sept cent cinquante euros"), (0, "zéro euro"),
    (123456, "mille deux cent trente-quatre euros et cinquante-six centimes"), (100_000_000, "un million d’euros"),
])
def test_amount_words(cents, expected):
    assert amount_words(cents) == expected


def test_amount_helpers():
    assert fmt_eur(123456) == "1 234,56 €"
    assert parse_amount("850,50") == 85050 and parse_amount("1 200") == 120000 and parse_amount("") == 0
    with pytest.raises(ValueError):
        parse_amount("abc")


def _pdf(**kw):
    q = pdfgen.QuittanceData(
        bailleur_nom="SCI Les Tilleuls", bailleur_adresse="12 rue des Lilas\n75011 Paris", locataire="Hélène Martin-Lefèvre",
        logement_adresse="3 av Jean Jaurès\n69007 Lyon", loyer=85050, charges=7000, du=date(2026, 10, 1), au=date(2026, 10, 31),
        date_emission=date(2026, 10, 10), ville="Paris", date_paiement=date(2026, 10, 5), mode_paiement="virement")
    for k, v in kw.items():
        setattr(q, k, v)
    return pdfgen.render(q)


def _text(pdf: bytes) -> str:
    doc = pdfium.PdfDocument(pdf)
    try:
        assert len(doc) == 1
        return doc[0].get_textpage().get_text_range()
    finally:
        doc.close()


def test_pdf_content():
    t = _text(_pdf())
    for s in ["QUITTANCE", "Octobre 2026", "SCI Les Tilleuls", "Hélène Martin-Lefèvre", "920,50", "neuf cent vingt euros et cinquante centimes",
              "01/10/2026", "31/10/2026", "Paiement reçu le 05/10/2026 par virement", "Fait à Paris, le 10/10/2026", "89-462"]:
        assert s in t.replace("\r\n", " ").replace("\n", " ").replace(" ", " ").replace("€", "€"), s


def test_pdf_markdown_injection_and_long_text():
    t = _text(_pdf(bailleur_nom="A **B** __C__ --D--", logement_adresse="Ligne\n" * 3)).replace("\r\n", " ")
    # dans la déclaration (rendu « markdown »), les marqueurs saisis restent du texte et ne changent pas la mise en forme
    assert "Je soussigné(e) A * *B* * _ _C_ _ - -D- -" in t


def test_period_helpers():
    assert pdfgen.period_label(date(2026, 2, 1), date(2026, 2, 28)) == "février 2026"
    assert pdfgen.period_phrase(date(2026, 10, 1), date(2026, 10, 31)) == "d’octobre 2026"
    assert pdfgen.period_phrase(date(2026, 4, 1), date(2026, 4, 15)) == "du 01/04/2026 au 15/04/2026"
    assert pdfgen.file_name("Martin-Lefèvre", date(2026, 10, 1)) == "quittance_MARTIN-LEFEVRE_2026-10.pdf"
