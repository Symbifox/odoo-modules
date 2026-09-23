"""Évaluateur d'expressions de la calculatrice.

Aucun ``eval`` : l'expression est découpée en jetons puis évaluée par une
descente récursive sur des ``Decimal``, donc ``0,1 + 0,2`` donne 0,3 et non
0,30000000000000004.

Ce qui est accepté, au-delà des quatre opérations :

* **les nombres à la française** : virgule ou point décimal, espaces (y compris
  insécables) ou apostrophes comme séparateurs de milliers. ``1 250,50``,
  ``1,250.50`` et ``12,5`` se lisent tous comme on s'y attend ;
* **les descriptifs** : ``6 (semaines) * 750 (dollars) =``. Une parenthèse qui
  ne contient aucun chiffre est un descriptif, pas un groupement ; les mots nus
  (``6 semaines * 750 $``) le sont aussi. Ils sont gardés à l'affichage et
  ignorés au calcul ;
* **un titre** en tête, séparé par un deux-points suivi d'une espace :
  ``Budget stagiaire : 6 semaines * 750 $`` ;
* **les durées** : ``1 h 45``, ``1h45``, ``2 heures``, ``1:45``, ``30 min``
  valent des heures décimales ;
* **les pourcentages** à la façon des calculatrices de bureau : ``200 + 10 %``
  vaut 220, ``200 * 10 %`` vaut 20 ;
* **une monnaie** (``$``, ``€``, ``£``, ``CA$``, ``USD``…) : si une seule apparaît,
  le résultat la porte. Aucune vérification d'unités n'est faite ;
* **une conversion** en fin d'expression : ``100 USD en CAD``, ``50 € + 20 $US
  en $``. Chaque montant qui porte une monnaie est converti au taux fourni par
  l'appelant (``rate``) ; un nombre nu (``3 * 100 USD en CAD``) ne l'est pas ;
* **des variables** : ``taux = 125`` range la variable (``Result.assign``),
  puis ``6 h * taux`` la lit. Un mot n'est une variable qu'en position
  d'opérande (début, après un opérateur ou une parenthèse) : dans
  ``6 semaines``, « semaines » reste un descriptif même si la variable existe.

Le module ne dépend de rien d'Odoo : les messages d'erreur sont des codes,
traduits par l'appelant.
"""

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, localcontext

MAX_LENGTH = 500
MAX_DEPTH = 40
MAX_EXPONENT = 64
MAX_MAGNITUDE = Decimal("1e30")

_SP = "[ \t\u00a0\u202f]"
_NUM = r"\d+(?:[ \u00a0\u202f']\d{3}(?!\d))*(?:[.,]\d+)*"
_WORD_END = r"(?![^\W\d_])"

_TITLE_RE = re.compile(r"^\s*([^\d()=:]*[^\W\d_][^\d()=:]*?)\s*:\s+(?=\S)")
_HOURS_RE = re.compile(
    rf"(\d+(?:[.,]\d+)?){_SP}*(?:heures|heure|hours|hour|hrs|hr|h){_WORD_END}"
    rf"(?:{_SP}*(\d{{1,2}})(?!\d)(?:{_SP}*(?:minutes|minute|min|mn|m){_WORD_END})?)?",
    re.IGNORECASE,
)
_MINUTES_RE = re.compile(
    rf"(\d+(?:[.,]\d+)?){_SP}*(?:minutes|minute|min|mn){_WORD_END}", re.IGNORECASE,
)
_CLOCK_RE = re.compile(r"(\d+):([0-5]\d)(?!\d)")
_NUMBER_RE = re.compile(_NUM)
_CURRENCY_RE = re.compile(r"(?:[A-Z]{2}\$|\$|€|£)")
_CONVERT_RE = re.compile(
    r"\s+(?:en|in|to|vers|→|->)\s+((?:[A-Za-z]{2})?\$|€|£|[A-Za-z]{3})\s*$", re.IGNORECASE)
_ASSIGN_RE = re.compile(r"^\s*([^\W\d_][\w]{0,29})\s*=\s*(?=\S)(.*)$", re.DOTALL)
_SYMBOL_CODES = {"$": None, "€": "EUR", "£": "GBP", "CA$": "CAD", "US$": "USD",
                 "NZ$": "NZD", "AU$": "AUD", "HK$": "HKD", "SG$": "SGD"}
#: Codes reconnus quand l'appelant n'en fournit pas.
DEFAULT_CURRENCIES = frozenset({"CAD", "USD", "EUR", "GBP", "NZD", "AUD", "JPY", "CHF"})
#: Mots qui ne peuvent pas nommer une variable.
RESERVED = frozenset({"x", "h", "hr", "hrs", "min", "mn", "m", "en", "in", "to", "vers",
                      "heure", "heures", "hour", "hours", "minute", "minutes"})
_WORD_RE = re.compile(r"[^\W\d_]+(?:['’.\-][^\W\d_]+)*\.?")
_SPACE_RE = re.compile(rf"{_SP}+")

_OPERATORS = {
    "+": "+", "-": "-", "\u2212": "-", "\u2013": "-",
    "*": "*", "\u00d7": "*", "\u00b7": "*",
    "/": "/", "\u00f7": "/",
    "^": "^",
}
_PRETTY = {"+": "+", "-": "\u2212", "*": "\u00d7", "/": "\u00f7", "^": "^"}


class CalcError(ValueError):
    """Erreur d'évaluation. ``code`` est stable, ``params`` complète le message."""

    def __init__(self, code, **params):
        self.code = code
        self.params = params
        super().__init__(code)


@dataclass
class Token:
    kind: str  # NUM, OP, LP, RP, PCT, LABEL, CUR
    text: str
    value: Decimal = None
    duration: bool = False
    code: str = ""  # CUR : code ISO résolu ; NUM : monnaie du montant
    paren: bool = False  # LABEL écrit entre parenthèses
    var: bool = False  # NUM tiré d'une variable


@dataclass
class Result:
    value: Decimal
    title: str = ""
    currency: str = ""
    duration: bool = False
    tokens: list = field(default_factory=list)
    assign: str = ""
    conversions: list = field(default_factory=list)  # [(de, vers, taux, date)]

    def display(self):
        """L'expression telle qu'elle sera relue : descriptifs compris,
        opérateurs typographiques, parenthèses de descriptif retirées."""
        out = ""
        prev = None
        for tok in self.tokens:
            if tok.kind == "OP":
                text = _PRETTY[tok.text]
            elif tok.kind == "CONV":
                text = tok.text
            else:
                text = tok.text
            glue = (
                prev is None
                or prev.kind == "LP"
                or tok.kind in ("RP", "PCT")
            )
            out += text if glue else " " + text
            prev = tok
        return out


def parse_number(raw, thousands_sep=","):
    """Interprète un nombre écrit à la main en ``Decimal``.

    Avec les deux séparateurs, le dernier est la décimale. Avec un seul, il est
    un séparateur de milliers s'il revient plusieurs fois, ou s'il est celui de
    la langue ET suivi d'exactement trois chiffres ; sinon c'est la décimale.
    En français (milliers = espace), ``12,5`` et ``12.5`` valent donc 12,5 ; en
    anglais, ``1,250`` vaut 1250 mais ``12,5`` vaut encore 12,5.
    """
    s = re.sub(r"[ '\u00a0\u202f]", "", raw)
    dots, commas = s.count("."), s.count(",")
    if dots and commas:
        dec = "." if s.rfind(".") > s.rfind(",") else ","
        thou = "," if dec == "." else "."
        if s.count(dec) > 1:
            raise CalcError("bad_number", text=raw)
        s = s.replace(thou, "").replace(dec, ".")
    elif dots or commas:
        sep = "." if dots else ","
        parts = s.split(sep)
        if len(parts) > 2:
            if not all(len(p) == 3 for p in parts[1:]):
                raise CalcError("bad_number", text=raw)
            s = "".join(parts)
        elif sep == thousands_sep and len(parts[1]) == 3:
            s = "".join(parts)
        else:
            s = parts[0] + "." + parts[1]
    try:
        return Decimal(s)
    except InvalidOperation as exc:
        raise CalcError("bad_number", text=raw) from exc


def split_title(text):
    match = _TITLE_RE.match(text)
    if not match:
        return "", text
    return match.group(1).strip(), text[match.end():]


def tokenize(text, thousands_sep=",", currencies=DEFAULT_CURRENCIES):
    tokens = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        space = _SPACE_RE.match(text, i)
        if space:
            i = space.end()
            continue
        m = _HOURS_RE.match(text, i)
        if m:
            hours = parse_number(m.group(1), thousands_sep)
            if m.group(2):
                if int(m.group(2)) > 59:
                    raise CalcError("bad_number", text=m.group(0))
                hours += Decimal(m.group(2)) / 60
            tokens.append(Token("NUM", _SPACE_RE.sub(" ", m.group(0)), hours, True))
            i = m.end()
            continue
        m = _MINUTES_RE.match(text, i)
        if m:
            value = parse_number(m.group(1), thousands_sep) / 60
            tokens.append(Token("NUM", _SPACE_RE.sub(" ", m.group(0)), value, True))
            i = m.end()
            continue
        m = _CLOCK_RE.match(text, i)
        if m:
            value = Decimal(m.group(1)) + Decimal(m.group(2)) / 60
            tokens.append(Token("NUM", m.group(0), value, True))
            i = m.end()
            continue
        m = _NUMBER_RE.match(text, i)
        if m:
            raw = m.group(0)
            tokens.append(Token("NUM", raw, parse_number(raw, thousands_sep)))
            i = m.end()
            continue
        if text.startswith("**", i):
            tokens.append(Token("OP", "^"))
            i += 2
            continue
        if ch in _OPERATORS:
            tokens.append(Token("OP", _OPERATORS[ch]))
            i += 1
            continue
        if ch == "%":
            tokens.append(Token("PCT", "%"))
            i += 1
            continue
        if ch == "(":
            close = _matching_paren(text, i)
            inner = text[i + 1:close] if close is not None else text[i + 1:]
            if close is not None and not re.search(r"\d", inner):
                label = inner.strip()
                if label:
                    tokens.append(Token("LABEL", label, paren=True))
                i = close + 1
                continue
            tokens.append(Token("LP", "("))
            i += 1
            continue
        if ch == ")":
            tokens.append(Token("RP", ")"))
            i += 1
            continue
        m = _CURRENCY_RE.match(text, i)
        if m:
            tokens.append(Token("CUR", m.group(0), code=_SYMBOL_CODES.get(m.group(0)) or ""))
            i = m.end()
            continue
        m = _WORD_RE.match(text, i)
        if m:
            if m.group(0) in ("x", "X"):
                # « 6 x 750 » : le x des claviers sans ×.
                tokens.append(Token("OP", "*"))
            elif m.group(0).upper() == m.group(0) and m.group(0) in currencies:
                tokens.append(Token("CUR", m.group(0), code=m.group(0)))
            else:
                tokens.append(Token("LABEL", m.group(0)))
            i = m.end()
            continue
        raise CalcError("unexpected", text=ch)
    return tokens


def _matching_paren(text, start):
    depth = 0
    for j in range(start, len(text)):
        if text[j] == "(":
            depth += 1
        elif text[j] == ")":
            depth -= 1
            if depth == 0:
                return j
    return None


def _bind_variables(tokens, variables):
    """Un descriptif en position d'opérande qui nomme une variable devient sa valeur."""
    if not variables:
        return tokens
    out, prev = [], None
    for tok in tokens:
        if (tok.kind == "LABEL" and tok.text in variables
                and (prev is None or prev.kind in ("OP", "LP"))):
            tok = Token("NUM", tok.text, Decimal(str(variables[tok.text])), var=True)
        out.append(tok)
        if tok.kind not in ("LABEL",):
            prev = tok
    return out


def _unknown_variable(tokens):
    """Le premier mot nu en position d'opérande : probablement une variable."""
    prev = None
    for tok in tokens:
        if tok.kind == "LABEL" and not tok.paren and prev is not None and prev.kind in ("OP", "LP"):
            return tok.text
        if tok.kind != "LABEL":
            prev = tok
    return None


def _bind_currencies(tokens, local):
    """Chaque montant prend la monnaie écrite juste après lui (« 100 USD »), ou
    juste avant (« $750 ») si ce symbole n'appartient pas déjà au nombre d'avant."""
    for i, tok in enumerate(tokens):
        if tok.kind != "NUM":
            continue
        after = tokens[i + 1] if i + 1 < len(tokens) else None
        before = tokens[i - 1] if i else None
        cur = None
        if after is not None and after.kind == "CUR":
            cur = after
        elif (before is not None and before.kind == "CUR"
              and not (i >= 2 and tokens[i - 2].kind == "NUM")):
            cur = before
        if cur is not None:
            tok.code = cur.code or local


class _Parser:
    def __init__(self, tokens):
        self.tokens = [t for t in tokens if t.kind not in ("LABEL", "CUR", "CONV")]
        self.pos = 0
        self.depth = 0

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def take(self):
        tok = self.peek()
        self.pos += 1
        return tok

    def run(self):
        if not self.tokens:
            raise CalcError("empty")
        value, _pct = self.expr()
        tok = self.peek()
        if tok is not None:
            if tok.kind in ("NUM", "LP"):
                raise CalcError("missing_operator", text=tok.text)
            if tok.kind == "RP":
                raise CalcError("unbalanced")
            raise CalcError("unexpected", text=tok.text)
        return value

    @staticmethod
    def _check(value):
        if abs(value) > MAX_MAGNITUDE:
            raise CalcError("too_large")
        return value

    def expr(self):
        left, pct = self.term()
        while (tok := self.peek()) is not None and tok.kind == "OP" and tok.text in "+-":
            self.take()
            right, right_pct = self.term()
            if right_pct:
                # « 200 + 10 % » : le pourcentage porte sur ce qui précède.
                right = left * right
            left = self._check(left + right if tok.text == "+" else left - right)
            pct = False
        return left, pct

    def term(self):
        left, pct = self.unary()
        while (tok := self.peek()) is not None and tok.kind == "OP" and tok.text in "*/":
            self.take()
            right, _ = self.unary()
            if tok.text == "/":
                if right == 0:
                    raise CalcError("division_by_zero")
                left = left / right
            else:
                left = left * right
            left = self._check(left)
            pct = False
        return left, pct

    def unary(self):
        tok = self.peek()
        if tok is not None and tok.kind == "OP" and tok.text in "+-":
            self.take()
            value, pct = self.unary()
            return (-value if tok.text == "-" else value), pct
        return self.power()

    def power(self):
        base, pct = self.postfix()
        tok = self.peek()
        if tok is not None and tok.kind == "OP" and tok.text == "^":
            self.take()
            exponent, _ = self.unary()
            if abs(exponent) > MAX_EXPONENT:
                raise CalcError("too_large")
            try:
                base = self._check(base ** exponent)
            except (InvalidOperation, ArithmeticError) as exc:
                raise CalcError("invalid_power") from exc
            pct = False
        return base, pct

    def postfix(self):
        value = self.primary()
        tok = self.peek()
        if tok is not None and tok.kind == "PCT":
            self.take()
            return value / 100, True
        return value, False

    def primary(self):
        tok = self.take()
        if tok is None:
            raise CalcError("missing_operand")
        if tok.kind == "NUM":
            # Un littéral de 499 chiffres passait sans opération : float() → inf.
            return self._check(tok.value)
        if tok.kind == "LP":
            self.depth += 1
            if self.depth > MAX_DEPTH:
                raise CalcError("too_deep")
            value, _ = self.expr()
            closing = self.take()
            if closing is None or closing.kind != "RP":
                raise CalcError("unbalanced")
            self.depth -= 1
            return value
        if tok.kind == "RP":
            raise CalcError("unbalanced")
        if tok.kind == "OP":
            raise CalcError("missing_operand")
        raise CalcError("unexpected", text=tok.text)


def evaluate(text, thousands_sep=",", variables=None, rate=None,
             currencies=DEFAULT_CURRENCIES, local="CAD"):
    """Évalue ``text`` et rend un ``Result``. Lève ``CalcError``.

    ``variables`` : {nom: valeur}. ``rate(de, vers)`` rend ``(taux, date)`` ou
    lève ``CalcError("no_rate")`` ; sans lui, une conversion est refusée.
    ``local`` : le code de la monnaie que désigne un ``$`` nu.
    """
    text = (text or "").strip()
    if len(text) > MAX_LENGTH:
        raise CalcError("too_long", limit=MAX_LENGTH)
    # Le « = » final des calculatrices, et celui du calcul natif d'Odoo en tête.
    text = text.lstrip("=").rstrip("= \t\u00a0\u202f")
    assign = ""
    match = _ASSIGN_RE.match(text)
    if match and not match.group(2).lstrip().startswith("="):
        name = match.group(1)
        if name.lower() in RESERVED or name in currencies:
            raise CalcError("bad_variable", text=name)
        assign, text = name, match.group(2)
    # Un « = » au milieu est refusé par le découpage, sauf dans un descriptif
    # entre parenthèses, où il n'est que du texte.
    title, body = split_title(text)
    target = ""
    conv = _CONVERT_RE.search(body)
    if conv:
        wanted = conv.group(1)
        code = _SYMBOL_CODES.get(wanted, wanted.upper()) if wanted in _SYMBOL_CODES \
            else wanted.upper()
        code = code or local
        if code not in currencies and code != local and wanted.isupper():
            raise CalcError("no_rate", text=wanted)
        if code in currencies or code == local:
            target = code
            conv_text = body[conv.start():].strip()
            body = body[:conv.start()]
    # Tout le calcul, découpage compris, dans le même contexte à 34 chiffres :
    # « 5 min * 3 » donnait 0,24999999999999999999999999999.
    with localcontext() as ctx:
        ctx.prec = 34
        tokens = tokenize(body, thousands_sep, currencies)
        tokens = _bind_variables(tokens, variables)
        _bind_currencies(tokens, local)
        conversions = []
        if target:
            if rate is None:
                raise CalcError("no_rate", text=target)
            for tok in tokens:
                if tok.kind == "NUM" and tok.code and tok.code != target:
                    factor, day = rate(tok.code, target)
                    tok.value = tok.value * factor
                    if (tok.code, target, factor, day) not in conversions:
                        conversions.append((tok.code, target, factor, day))
            if not any(t.kind == "NUM" and t.code for t in tokens):
                # « 100 usd en cad » : rien à convertir, et le taire rendait 100.
                raise CalcError("nothing_to_convert", text=target)
            tokens.append(Token("CONV", conv_text))
        try:
            value = _Parser(tokens).run()
        except CalcError as err:
            # « 6 h * taux » sans variable « taux » : le vrai problème est le nom.
            unknown = _unknown_variable(tokens) if err.code in ("missing_operand", "empty") \
                else None
            if unknown:
                raise CalcError("unknown_variable", text=unknown) from err
            raise
    currencies_seen = {t.code or t.text for t in tokens if t.kind == "CUR"}
    durations = any(t.kind == "NUM" and t.duration for t in tokens)
    # Une variable a une unité qu'on ignore (« 6 h * taux » est un montant).
    if any(t.kind == "NUM" and t.var for t in tokens):
        durations = False
    if target:
        currency = "$" if target == local else target
    elif len(currencies_seen) == 1:
        currency = next(t.text for t in tokens if t.kind == "CUR")
    else:
        currency = ""
    return Result(
        value=value,
        title=title,
        currency=currency,
        duration=durations and not currencies_seen and not target,
        tokens=tokens,
        assign=assign,
        conversions=conversions,
    )


def parse_amount(text, thousands_sep=",", variables=None):
    """Un montant saisi dans un champ de formulaire (taxes, pourcentages) :
    une expression complète est acceptée, ``=1000*3`` et les variables compris."""
    return evaluate(text, thousands_sep, variables=variables).value


def parse_column(text, thousands_sep=","):
    """Les nombres d'une colonne collée (tableur, courriel). Une cellule par
    ligne, ou plusieurs séparées par tabulation ou point-virgule. Les cellules
    qui ne sont pas un nombre (en-têtes, totaux écrits en lettres) sont
    comptées comme ignorées. ``(125,00)`` est négatif, comme en comptabilité.
    """
    values, skipped = [], 0
    for line in (text or "").splitlines():
        for cell in re.split(r"[\t;]", line):
            cell = cell.strip()
            if not cell:
                continue
            negative = cell.startswith("(") and cell.endswith(")")
            core = cell[1:-1] if negative else cell
            core = _CURRENCY_RE.sub("", core).replace("%", "").strip()
            sign = Decimal(1)
            if core[:1] in ("-", "\u2212"):
                sign, core = Decimal(-1), core[1:].strip()
            match = _NUMBER_RE.fullmatch(core)
            if not match:
                skipped += 1
                continue
            try:
                value = parse_number(core, thousands_sep) * sign
            except CalcError:
                skipped += 1
                continue
            values.append(-value if negative else value)
    return values, skipped
