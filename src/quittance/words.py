"""Montants en lettres, en français (orthographe traditionnelle).

Même règles que la version web : « soixante et onze », « quatre-vingts »,
« deux cents » / « deux cent un », « mille » invariable, « un million d'euros ».
"""

_U = ["zéro", "un", "deux", "trois", "quatre", "cinq", "six", "sept", "huit", "neuf", "dix",
      "onze", "douze", "treize", "quatorze", "quinze", "seize", "dix-sept", "dix-huit", "dix-neuf"]
_T = ["", "", "vingt", "trente", "quarante", "cinquante", "soixante"]


def _below100(n: int) -> str:
    if n < 20:
        return _U[n]
    t, u = divmod(n, 10)
    if t == 7:
        return "soixante et onze" if u == 1 else "soixante-" + _U[10 + u]
    if t == 8:
        return "quatre-vingts" if u == 0 else "quatre-vingt-" + _U[u]
    if t == 9:
        return "quatre-vingt-" + _U[10 + u]
    if u == 0:
        return _T[t]
    if u == 1:
        return _T[t] + " et un"
    return _T[t] + "-" + _U[u]


def _below1000(n: int) -> str:
    h, r = divmod(n, 100)
    s = ""
    if h > 0:
        s = "cent" if h == 1 else _U[h] + " cent" + ("s" if r == 0 else "")
    if r > 0:
        s += (" " if s else "") + _below100(r)
    return s


def number_words(n: int) -> str:
    if n < 0:
        raise ValueError("montant négatif")
    if n == 0:
        return "zéro"
    parts = []
    mil, rest = divmod(n, 1_000_000)
    th, r = divmod(rest, 1000)
    if mil:
        parts.append("un million" if mil == 1 else _below1000(mil) + " millions")
    if th:
        w = "mille" if th == 1 else _below1000(th)
        if th != 1:
            for suffix in ("cents", "vingts"):  # « deux cent mille », « quatre-vingt mille »
                if w.endswith(suffix):
                    w = w[:-1]
            w += " mille"
        parts.append(w)
    if r:
        parts.append(_below1000(r))
    return " ".join(parts)


def amount_words(cents: int) -> str:
    """Montant en centimes → « huit cent cinquante euros et vingt centimes »."""
    e, c = divmod(int(cents), 100)
    s = ""
    if e > 0 or c == 0:
        if e >= 1_000_000 and e % 1_000_000 == 0:
            unit = " d’euros"
        else:
            unit = " euros" if e >= 2 else " euro"
        s = number_words(e) + unit
    if c > 0:
        s += (" et " if s else "") + number_words(c) + (" centimes" if c >= 2 else " centime")
    return s


def fmt_eur(cents: int) -> str:
    """920,50 € (espaces insécables pour les milliers)."""
    neg = cents < 0
    e, c = divmod(abs(int(cents)), 100)
    es = f"{e:,}".replace(",", " ")
    return ("-" if neg else "") + f"{es},{c:02d} €"


MAX_CENTS = 1_000_000_000  # 10 millions d'euros : au-delà, saisie manifestement erronée


def parse_amount(value) -> int:
    """« 850,50 » / « 850.5 » / 850 → centimes. Lève ValueError si invalide, négatif ou démesuré."""
    if value is None or value == "":
        return 0
    if isinstance(value, bool):
        raise ValueError("montant invalide")
    if isinstance(value, (int, float)):
        n = round(value * 100)
    else:
        s = str(value).strip().replace(" ", "").replace(" ", "").replace(" ", "").replace("€", "").replace(",", ".")
        if s == "":
            return 0
        try:
            n = round(float(s) * 100)
        except OverflowError as e:
            raise ValueError("montant invalide") from e
    if n < 0:
        raise ValueError("montant négatif")
    if n > MAX_CENTS:
        raise ValueError("montant trop élevé")
    return n
