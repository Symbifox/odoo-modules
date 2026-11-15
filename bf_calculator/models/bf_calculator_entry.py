"""Historique de la calculatrice, et les calculs eux-mêmes.

Le calcul se fait ici, côté serveur, pour une seule raison : il n'y a qu'un
évaluateur (``lib/expression.py``), éprouvé en Python, et l'aperçu du panneau
lit exactement ce que l'historique gardera. Un aller-retour RPC par frappe
(amorti) coûte moins qu'un second évaluateur en JavaScript qui divergerait.

Chaque personne ne voit que ses lignes : la règle est globale, elle vaut aussi
pour les administrateurs. Ce qui est versé au chatter, en revanche, suit les
droits de la fiche qui le reçoit.
"""

import logging
import re
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools.misc import format_date

from ..lib import dates as caldates
from ..lib import expression as calc

_logger = logging.getLogger(__name__)

CENT = Decimal("0.01")
HISTORY_DAYS_PARAM = "bf_calculator.history_days"
DEFAULT_HISTORY_DAYS = 90
MAX_HISTORY_LIMIT = 200
EASTER_PARAM = "bf_calculator.easter_holiday"  # friday | monday
#: Arrondis offerts : pas → libellé de la note.
ROUNDINGS = {"0.01": "0.01", "0.05": "0.05", "1": "1"}
#: Un code de devise ou une conversion : seul cas où les taux sont lus.
#: Codes en MAJUSCULES seulement : « 3 (ans) * 5 » ou « 2 par jour » ne doivent pas
#: déclencher la lecture des taux (relecture adverse, 2026-09-22).
_CURRENCY_HINT = re.compile(
    r"\b[A-Z]{3}\b|(?i:\b(?:en|in|to|vers)\s+)(?:[A-Za-z]{3}|[A-Z]{0,2}\$|€|£)\s*$")
#: Champs qu'un appel RPC ne peut pas poser lui-même.
_PROTECTED = ("message_id", "user_id")

# Repli quand la comptabilité n'est pas installée, ou n'a aucune taxe de vente
# utilisable : les taux du Québec au 2026-09-22.
_DEFAULT_QC = [("TPS", Decimal("5")), ("TVQ", Decimal("9.975"))]
_QC_HINTS = ("TVQ", "QST")
#: Mots qui désignent « toutes les taxes du profil choisi », dans les deux langues.
_TAX_ALL = ("taxes", "taxe", "tax")
#: Une taxe se nomme dans la langue de la base : les deux noms valent toujours.
_TAX_ALIASES = {"tvq": "qst", "qst": "tvq", "tps": "gst", "gst": "tps",
                "tvh": "hst", "hst": "tvh", "tvp": "pst", "pst": "tvp"}
#: Seul cas où les taxes de la comptabilité sont lues : un mot de taxe précédé
#: de + ou -. Sans cet indice, chaque frappe de l'aperçu ferait une recherche
#: sur account.tax, comme pour les taux de change.
_TAX_HINT = re.compile(
    r"[-+\u2212\u2013]\s*(?:" + "|".join(sorted(set(_TAX_ALL) | set(_TAX_ALIASES))) + r")\b",
    re.IGNORECASE)
#: Les mots qu'une variable ne peut pas porter, et ceux dont le mauvais
#: opérateur se nomme. La liste est FIGÉE : elle ne coûte aucune lecture de
#: base, et le modèle la passe TOUJOURS à l'évaluateur.
#: 🔴 Auparavant, l'évaluateur ne connaissait ces mots que par
#: ``taxes``, injecté seulement quand l'expression porte un mot de taxe précédé
#: de + ou - : une affectation n'en porte jamais, donc « tvq = 9,975 » était
#: acceptée, puis « 1 000 * tvq » valait 9 975 (la variable) alors que
#: « 1 000 + tvq » valait 1 099,75 (la taxe). Le refus du mauvais opérateur,
#: lui, était inatteignable par le vrai chemin.
TAX_WORDS = frozenset(_TAX_ALL) | frozenset(_TAX_ALIASES)
#: Profil de taxe à taux tapés à la main.
MANUAL_PROFILE = "manual"
MAX_MANUAL_RATES = 4
#: Un taux tapé à la main : UN seul nombre, décimale « . » ou « , », un « % »
#: facultatif, des espaces autour. Tout le reste est refusé en nommant le
#: morceau fautif.
#: 🔴 La virgule n'est PAS un séparateur de taux : en français c'est la
#: décimale, et « 9,975 » doit rester un seul taux. Auparavant, les
#: morceaux n'étaient pas validés : « 5, 9.975 » se lisait « 5,9.975 », soit
#: 59,975 %, sans refus ; et « 1e-40 » passait la borne ]0, 100] pour
#: s'afficher « 0 % ».
_MANUAL_RATE_RE = re.compile(r"^\s*\d+(?:[.,]\d+)?\s*%?\s*$")
#: Les nombres écrits dans un nom de taxe, pour savoir si le nom porte déjà son
#: taux : un taux vaut au plus 100, donc aucun séparateur de milliers à prévoir.
_NAME_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")


def _plain(value):
    """« 4500 », jamais « 4.5E+3 » : ce texte finit dans un champ de saisie."""
    text = format(Decimal(value).normalize(), "f")
    return text


def _cents(value):
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _is_qc(components):
    return any(h in (name or "").upper() for name, _r in components for h in _QC_HINTS)


def _tax_slug(name):
    """Le mot d'un nom de taxe, sans taux ni ponctuation : « TVP 6% » → « tvp »,
    « 9.975% TVQ » → « tvq ». Rend "" si le nom n'a aucune lettre, ou s'il en a
    plusieurs mots (un mot-clé doit se taper d'un trait)."""
    mots = re.findall(r"[^\W\d_]+", name or "")
    return mots[0].lower() if len(mots) == 1 else ""


class BfCalculatorEntry(models.Model):
    _name = "bf.calculator.entry"
    _description = "Calculator history line"
    _order = "create_date desc, id desc"
    _rec_name = "display_text"

    user_id = fields.Many2one(
        "res.users", string="User", required=True, index=True, ondelete="cascade",
        default=lambda self: self.env.user,
    )
    title = fields.Char(string="Title")
    expression = fields.Char(string="Expression", required=True)
    display_text = fields.Char(string="Calculation")
    result = fields.Float(string="Result", digits=(16, 6))
    result_text = fields.Char(string="Formatted result")
    mode = fields.Selection(
        selection=[
            ("standard", "Standard"),
            ("hours", "Hours"),
            ("tax", "Taxes"),
            ("percent", "Percentages"),
            ("column", "Column"),
            ("date", "Dates"),
        ],
        string="Mode", default="standard", required=True,
    )
    res_model = fields.Char(string="Origin model")
    res_id = fields.Many2oneReference(string="Origin record", model_field="res_model")
    message_id = fields.Many2one(
        "mail.message", string="Posted as", ondelete="set null", readonly=True,
    )
    pinned = fields.Boolean(string="Pinned")
    note = fields.Char(string="Note", help="Exchange rate used, rounding applied.")
    residual = fields.Char(
        string="Residual value",
        help="The result as it stays in the calculator field, ready for the next operation.")

    # ------------------------------------------------------------------
    # Gardes
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        # À la création, la règle globale est vérifiée APRÈS : un user_id étranger
        # y est refusé. Seul message_id doit être bloqué ici.
        self._check_protected(vals_list, fields=("message_id",))
        return super().create(vals_list)

    def write(self, vals):
        # 🔴 À l'écriture, Odoo ne vérifie la règle que sur l'état d'AVANT : écrire
        # user_id déposait une ligne dans l'historique de quelqu'un d'autre.
        self._check_protected([vals])
        return super().write(vals)

    def _check_protected(self, vals_list, fields=_PROTECTED):
        """🔴 `message_id` posé par RPC puis relu par `web_read` donnait le nom
        de la fiche de N'IMPORTE QUEL message (le display_name d'un many2one se
        lit en sudo) : un interne énumérait ainsi toute la base. Seul le code du
        module, en sudo, le pose (relecture adverse, 2026-09-22)."""
        if self.env.su:
            return
        for vals in vals_list:
            if any(f in vals for f in fields):
                raise AccessError(_("This field is set by the calculator only."))

    def _check_internal(self):
        """`call_kw` n'applique pas les droits du modèle avant l'appel : sans ce
        contrôle, un usager portail lisait les taxes (sudo) et déclenchait la
        lecture des taux de change."""
        if not self.env.su and not self.env.user._is_internal():
            raise AccessError(_("The calculator is for internal users."))

    # ------------------------------------------------------------------
    # Mise en forme
    # ------------------------------------------------------------------
    def _lang(self):
        """🔴 `_lang_get` d'une langue INACTIVE rend un recordset VIDE, et tout
        `lang.format(...)` tombe alors en « Expected singleton ». Mesuré :
        sur une base en production, **en_US n'était pas active**
        (deux langues seulement), donc une session en en_US faisait tomber
        chaque calcul, chaque taxe et chaque aperçu. Le repli prend une langue
        active.

        🔴 Et il la CHOISIT. ``search(limit=1)`` prenait la première langue
        active PAR ORDRE DE NOM (``_order = "active desc,name"``) : sur une base
        dont la première langue active n'est pas anglaise, une session anglaise
        lisait et écrivait les nombres à la française sans rien dire (mesuré :
        « 1,250 + 10 » rendait 11,25 au lieu de 1 260, et un
        montant sortait « $1 000,00 »). On préfère donc une langue active de la
        MÊME FAMILLE que la session, puis n'importe quelle langue anglaise,
        puis la première active.
        """
        Lang = self.env["res.lang"]
        for code in (self.env.lang, "en_US"):
            lang = Lang._lang_get(code) if code else Lang
            if lang:
                return lang
        actives = Lang.search([("active", "=", True)])
        for famille in ((self.env.lang or "en_US")[:2], "en"):
            proche = actives.filtered(lambda l, f=famille: (l.code or "")[:2] == f)
            if proche:
                return proche[0]
        return actives[:1]

    def _variables(self):
        """Les variables de la personne, SANS celles qui portent un mot de taxe
        réservé.

        🔴 ``TAX_WORDS`` ne ferme que l'affectation NEUVE. Une variable DÉJÀ
        rangée sous un nom de taxe gardait donc les deux sens (mesuré :
        « 1 000 * tvq » valait 9 975, la variable, et
        « 1 000 + tvq » valait 1 099,75, la taxe, dans la même session).
        L'évaluateur ne la reçoit plus : le mot redevient une taxe partout, et
        « 1 000 * tvq » dit maintenant qu'une taxe s'applique avec + ou -. La
        pastille reste dans le panneau, et la personne peut la supprimer.
        """
        return {nom: valeur
                for nom, valeur in self.env["bf.calculator.variable"]._as_dict().items()
                if nom.lower() not in TAX_WORDS}

    def _thousands_sep(self):
        lang = self._lang()
        return (lang.thousands_sep if lang else ",") or ","

    def _format_number(self, value, decimals=None):
        """Nombre à la façon de la langue de la personne. Sans ``decimals``,
        jusqu'à dix décimales, zéros de queue retirés."""
        lang = self._lang()
        value = Decimal(value)
        if decimals is None:
            text = lang.format("%.10f", float(value), grouping=True)
            point = lang.decimal_point or "."
            if point in text:
                text = text.rstrip("0").rstrip(point)
            if text in ("-0", ""):
                text = "0"
            return text
        return lang.format(f"%.{decimals}f", float(_cents(value) if decimals == 2 else value),
                           grouping=True)

    def _format_money(self, value, symbol="$"):
        text = self._format_number(value, 2)
        if (self.env.lang or "en_US").startswith("fr"):
            return f"{text}\u00a0{symbol}"
        if value < 0:
            return f"-{symbol}{text.lstrip('-')}"
        return f"{symbol}{text}"

    @staticmethod
    def _format_duration(hours):
        sign = "-" if hours < 0 else ""
        minutes_total = int((abs(Decimal(hours)) * 60).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        h, m = divmod(minutes_total, 60)
        return f"{sign}{h} h {m:02d}"

    def _format_result(self, res):
        if res.currency:
            return self._format_money(res.value, res.currency)
        text = self._format_number(res.value)
        if res.duration:
            return f"{text} h ({self._format_duration(res.value)})"
        return text

    def _error_message(self, err):
        messages = {
            "empty": _("Type a calculation."),
            "too_long": _("The calculation is too long (%(limit)s characters at most).",
                          limit=err.params.get("limit")),
            "unexpected": _("Unexpected character: %(text)s", text=err.params.get("text", "")),
            "missing_operator": _("An operator is missing before %(text)s.",
                                  text=err.params.get("text", "")),
            "missing_operand": _("A number is missing."),
            "unbalanced": _("A parenthesis is not closed, or closed once too often."),
            "division_by_zero": _("Division by zero."),
            "too_deep": _("Too many nested parentheses."),
            "too_large": _("The result is too large."),
            "invalid_power": _("This power cannot be computed."),
            "bad_number": _("Unreadable number: %(text)s", text=err.params.get("text", "")),
            "bad_variable": _("“%(text)s” cannot be a variable name.", text=err.params.get("text", "")),
            "no_rate": _("No exchange rate for %(text)s.", text=err.params.get("text", "")),
            "nothing_to_convert": _("Nothing to convert to %(text)s: write currency codes in capitals (100 USD).",
                                    text=err.params.get("text", "")),
            "unknown_variable": _("“%(text)s” is not a known variable.", text=err.params.get("text", "")),
            "ambiguous_tax": _("Several taxes are named “%(text)s”: pick a profile in the Taxes tab.",
                               text=err.params.get("text", "")),
            "tax_needs_amount": _("An amount is missing before “%(text)s”.",
                                  text=err.params.get("text", "")),
            "tax_bad_operator": _("A tax applies with + or -: 1 000 + %(text)s.",
                                  text=err.params.get("text", "")),
            "no_manual_rate": _("Type a rate, for example 9.975."),
            "too_many_rates": _("%(limit)s rates at most.", limit=err.params.get("limit")),
            "bad_tax_rate": _("Unusable tax rate: %(text)s", text=err.params.get("text", "")),
            "bad_rate_part": _("“%(text)s” is not a single rate: separate the taxes "
                               "with +, as in 5 + 9.975.", text=err.params.get("text", "")),
            "unknown_profile": _("This tax profile is no longer available: "
                                 "choose one in the Taxes tab."),
        }
        return messages.get(err.code, err.code)

    def _payload(self):
        return [{
            "id": e.id,
            "title": e.title or "",
            "expression": e.expression,
            "display": e.display_text or e.expression,
            "result": e.result,
            "result_text": e.result_text or "",
            "mode": e.mode,
            "posted": bool(e.message_id),
            "pinned": e.pinned,
            "note": e.note or "",
            "residual": e.residual or "",
            "date": fields.Datetime.to_string(e.create_date),
        } for e in self]

    def _chatter_line(self):
        """Une ligne de chatter : « Titre : calcul = résultat (note) »."""
        self.ensure_one()
        line = f"{self.display_text or self.expression} = {self.result_text}"
        if self.note:
            line += f" ({self.note})"
        return f"{self.title} : {line}" if self.title else line

    # ------------------------------------------------------------------
    # Calcul (appelé par le panneau)
    # ------------------------------------------------------------------
    def _residual(self, value, currency="", duration=False):
        """Le résultat tel qu'il reste dans le champ, prêt pour l'opération
        suivante : au format de la langue (« 4 500 »), que l'évaluateur relit,
        sans décimales forcées, avec son unité."""
        text = self._format_number(value)
        if currency:
            return f"{text} {currency}"
        if duration:
            return f"{text} h"
        return text

    @staticmethod
    def _round(value, rounding):
        step = ROUNDINGS.get(rounding or "")
        if not step:
            return value
        step = Decimal(step)
        return (value / step).quantize(Decimal(1), rounding=ROUND_HALF_UP) * step

    @api.model
    def _tax_keywords(self, profile_key=False, rates_text=""):
        """Les mots de taxe acceptés dans une opération : ``taxes`` (le profil
        choisi, taux libre compris) et chaque taxe nommée des profils de la
        société (``tvq``, ``tps``, et leurs noms anglais). Une liste vide
        signale un mot qui désigne plusieurs taux (``tvh`` quand la base en
        porte trois) : c'est l'évaluateur qui le refuse, en le nommant.

        🔴 Le taux libre s'applique AUSSI dans l'opération. Auparavant,
        « taxes » retombait en silence sur le premier profil
        comptable quand le panneau était resté sur le taux libre : l'onglet
        Taxes rendait 1 200,00 $ pour un taux libre de 20 % sur 1 000, et
        l'opération « 1 000 + taxes » rendait 1 149,75 dans le même panneau,
        pour le même profil choisi. Des taux tapés inutilisables refusent
        maintenant le calcul ; ils ne changent jamais de profil."""
        profiles = [p for p in self._tax_profiles() if p["key"] != MANUAL_PROFILE]
        if profile_key == MANUAL_PROFILE:
            # Lève sur un morceau illisible : le refus de l'onglet Taxes, mot
            # pour mot, plutôt qu'un repli muet sur la comptabilité.
            keywords = {word: list(self._manual_components(rates_text))
                        for word in _TAX_ALL}
        else:
            # Aucune branche « la liste est vide » : ``_tax_profiles`` rend
            # toujours au moins le repli du Québec, et un essai le garde.
            selected = self._pick_profile(profiles, profile_key)
            keywords = {word: list(selected["components"]) for word in _TAX_ALL}
        par_mot = {}
        for profile in profiles:
            for name, rate in profile["components"]:
                slug = _tax_slug(name)
                if not slug or slug in _TAX_ALL:
                    continue
                par_mot.setdefault(slug, {})[rate] = name
        for slug, rates in par_mot.items():
            for word in (slug, _TAX_ALIASES.get(slug)):
                if not word or word in _TAX_ALL:
                    continue
                if len(rates) == 1:
                    rate, name = next(iter(rates.items()))
                    keywords.setdefault(word, [(name, rate)])
                else:
                    keywords.setdefault(word, [])
        return keywords

    def _pick_profile(self, profiles, profile_key):
        """Le profil demandé. Une clé VIDE garde le repli sur le premier : c'est
        la première ouverture du panneau, rien n'a encore été choisi. Une clé
        non vide et INCONNUE refuse en disant où choisir.

        🔴 Mesuré : ``tax:999999`` rendait 1 149,75 et annonçait
        « 14.975% TPS+TVQ ». Répondre pour un autre profil que celui demandé est
        le défaut corrigé ailleurs ; il ne survit pas ici. Le panneau
        n'y tombe pas en usage normal : l'onglet Taxes recale son menu avant
        tout calcul, et le champ d'opération ne lit un profil que si
        l'expression porte un mot de taxe.
        """
        if not profile_key:
            return profiles[0]
        found = next((p for p in profiles if p["key"] == profile_key), None)
        if found is None:
            raise calc.CalcError("unknown_profile")
        return found

    def _tax_op(self):
        """L'arithmétique de l'opération « + taxes » : celle de l'onglet Taxes,
        au cent et par taxe, et non une simple multiplication. Les deux chemins
        doivent donner le même montant pour le même total."""
        def apply_tax(value, components, sign):
            direction = "forward" if sign == "+" else "reverse"
            base, taxes, total, exact = self._tax_breakdown(value, components, direction)
            return (total if sign == "+" else base), taxes, exact

        return apply_tax

    def _evaluate(self, expression, rounding=False, tax_profile=False, rates_text=""):
        Rates = self.env["bf.calculator.rates"]
        wants_rates = bool(_CURRENCY_HINT.search(expression or ""))
        currencies = Rates._currencies() if wants_rates else calc.DEFAULT_CURRENCIES
        wants_taxes = bool(_TAX_HINT.search(expression or ""))
        taxes = self._tax_keywords(tax_profile, rates_text) if wants_taxes else None

        def rate(src, dst):
            found = Rates._rate(src, dst)
            if not found:
                raise calc.CalcError("no_rate", text=src if src != "CAD" else dst)
            return found

        res = calc.evaluate(
            expression, self._thousands_sep(),
            variables=self._variables(),
            rate=rate, currencies=currencies,
            taxes=taxes, tax_op=self._tax_op() if wants_taxes else None,
            # Toujours passée, et sans lecture de base : c'est elle qui refuse
            # un nom de variable et qui nomme le mauvais opérateur.
            tax_words=TAX_WORDS,
        )
        notes = []
        for src, dst, factor, day in res.conversions:
            notes.append(_("1 %(src)s = %(rate)s %(dst)s, Bank of Canada, %(day)s",
                           src=src, rate=self._format_number(factor.quantize(Decimal("0.0001"))),
                           dst=dst, day=day))
        for step in res.tax_steps:
            detail = "; ".join(
                f"{name} ({self._format_number(rate_)}\u00a0%) {self._format_money(amount)}"
                for name, rate_, amount in step["taxes"])
            notes.append(_("taxes added: %(detail)s", detail=detail) if step["sign"] == "+"
                         else _("taxes removed: %(detail)s", detail=detail))
            if not step["exact"]:
                notes.append(_("no before-tax amount gives exactly this total "
                               "once each tax is rounded"))
        rounded = self._round(res.value, rounding)
        if rounded != res.value:
            notes.append(_("rounded to %(step)s", step=self._format_number(Decimal(rounding))))
            res.value = rounded
        return res, self._format_result(res), "; ".join(notes)

    @api.model
    def calc_evaluate(self, expression, rounding=False, tax_profile=False, rates_text=""):
        """Aperçu : n'écrit rien. ``rates_text`` porte les taux tapés à la main
        quand le profil choisi est le taux libre."""
        self._check_internal()
        try:
            res, text, note = self._evaluate(expression, rounding, tax_profile, rates_text)
        except calc.CalcError as err:
            return {"ok": False, "error": self._error_message(err)}
        display = res.display()
        return {
            "ok": True,
            "value": float(res.value),
            "raw": _plain(res.value),
            "result_text": text,
            "display": f"{res.assign} = {display}" if res.assign else display,
            "title": res.title,
            "note": note,
            "assign": res.assign,
            "residual": self._residual(res.value, res.currency, res.duration),
        }

    def _origin_vals(self, res_model, res_id):
        if res_model and res_id and res_model in self.env and isinstance(res_id, int):
            return {"res_model": res_model, "res_id": res_id}
        return {}

    @api.model
    def calc_record(self, expression, title=False, res_model=False, res_id=False,
                    rounding=False, tax_profile=False, rates_text=""):
        """Calcule et range dans l'historique. Le titre saisi à part l'emporte
        sur celui écrit en tête de l'expression. « nom = … » range aussi la
        variable."""
        self._check_internal()
        try:
            res, text, note = self._evaluate(expression, rounding, tax_profile, rates_text)
        except calc.CalcError as err:
            return {"ok": False, "error": self._error_message(err)}
        if res.assign:
            self.env["bf.calculator.variable"]._set(res.assign, res.value)
        display = res.display()
        residual = self._residual(res.value, res.currency, res.duration)
        entry = self.create({
            "title": (title or res.title or "").strip()[:200] or False,
            "expression": expression.strip()[:calc.MAX_LENGTH],
            "display_text": f"{res.assign} = {display}" if res.assign else display,
            "result": float(res.value),
            "result_text": text,
            "note": note or False,
            "residual": residual,
            "mode": "hours" if res.duration else "standard",
            **self._origin_vals(res_model, res_id),
        })
        return {"ok": True, "entry": entry._payload()[0], "raw": _plain(res.value),
                "residual": residual, "variables": self.calc_variables()}

    # ------------------------------------------------------------------
    # Variables et mémoire
    # ------------------------------------------------------------------
    @api.model
    def calc_variables(self):
        self._check_internal()
        Var = self.env["bf.calculator.variable"]
        return [{"name": v.name, "value": v.value, "text": self._format_number(v.value),
                 "residual": self._residual(Decimal(v.value_exact or str(v.value)))}
                for v in Var.search([("user_id", "=", self.env.uid)])]

    @api.model
    def calc_delete_variable(self, name):
        self._check_internal()
        self.env["bf.calculator.variable"].search(
            [("user_id", "=", self.env.uid), ("name", "=", name)]).unlink()
        return self.calc_variables()

    @api.model
    def calc_memory(self, op, raw="0"):
        """Touches M+, M−, MC : la mémoire est la variable « M »."""
        self._check_internal()
        Var = self.env["bf.calculator.variable"]
        memory = Var.search([("user_id", "=", self.env.uid), ("name", "=", "M")], limit=1)
        if op == "clear":
            memory.unlink()
        elif op in ("add", "sub"):
            try:
                value = Decimal(str(raw))
                if not value.is_finite() or abs(value) > calc.MAX_MAGNITUDE:
                    raise ValueError
            except Exception:
                return {"ok": False, "error": _("Unreadable number: %(text)s", text=raw)}
            current = Decimal(memory.value_exact or str(memory.value)) if memory else Decimal(0)
            Var._set("M", current + value if op == "add" else current - value)
        return {"ok": True, "variables": self.calc_variables()}

    def calc_toggle_pin(self):
        self._check_internal()
        for entry in self.filtered(lambda e: e.user_id == self.env.user):
            entry.sudo().pinned = not entry.pinned
        return True

    # ------------------------------------------------------------------
    # Dates
    # ------------------------------------------------------------------
    def _format_day(self, day):
        return format_date(self.env, day, date_format="EEEE d MMMM y")

    @api.model
    def calc_dates(self, kind, a, b=False, n=0, business=False, record=False,
                   res_model=False, res_id=False):
        """``between`` : jours de ``a`` à ``b``. ``add`` : ``a`` + ``n`` jours
        (ouvrables si ``business``). Fériés : ceux de la CNESST (lib/dates.py)."""
        self._check_internal()
        easter_day = self.env["ir.config_parameter"].sudo().get_param(EASTER_PARAM, "friday")
        try:
            start = fields.Date.to_date(a)
            if not start:
                raise ValueError
        except (ValueError, TypeError):
            return {"ok": False, "error": _("Choose a date.")}
        rows, skipped = [], []
        try:
            if kind == "between":
                end = fields.Date.to_date(b) if b else None
                if not end:
                    return {"ok": False, "error": _("Choose a date.")}
                days = (end - start).days
                open_days, skipped = caldates.business_days_between(start, end, easter_day)
                rows = [(_("Calendar days"), str(days)), (_("Business days"), str(open_days))]
                display = _("From %(a)s to %(b)s", a=self._format_day(start),
                            b=self._format_day(end))
                value = open_days if business else days
                result_text = (_("%(n)s business days", n=open_days) if business
                               else _("%(n)s days", n=days))
            elif kind == "add":
                try:
                    n = int(n or 0)
                except (TypeError, ValueError):
                    return {"ok": False, "error": _("Unreadable number of days.")}
                if business:
                    end, skipped = caldates.add_business_days(start, n, easter_day)
                    display = _("%(a)s + %(n)s business days", a=self._format_day(start), n=n)
                else:
                    if abs(n) > caldates.MAX_DAYS:
                        raise ValueError("too_far")
                    end = start + timedelta(days=n)
                    display = _("%(a)s + %(n)s days", a=self._format_day(start), n=n)
                value = (end - start).days
                result_text = self._format_day(end)
                rows = [(_("Date"), result_text)]
            else:
                return {"ok": False, "error": _("Unknown calculation.")}
        except (ValueError, OverflowError):
            return {"ok": False, "error": _("Ten years at most.")}
        holidays = [f"{self._format_day(d)} ({name})" for d, name in skipped]
        payload = {
            "ok": True,
            "rows": [{"label": l, "value": v} for l, v in rows],
            "holidays": holidays,
            "display": display,
            "result_text": result_text,
            "raw": str(value),
        }
        if record:
            entry = self.create({
                "expression": f"{a} ; {b or n}"[:calc.MAX_LENGTH],
                "display_text": display,
                "result": float(value),
                "result_text": result_text,
                "note": (_("holidays skipped: %(list)s", list=", ".join(holidays))
                         if holidays else False),
                "mode": "date",
                **self._origin_vals(res_model, res_id),
            })
            payload["entry"] = entry._payload()[0]
        return payload

    @api.model
    def calc_history(self, search="", limit=20):
        self._check_internal()
        limit = max(1, min(int(limit or 20), MAX_HISTORY_LIMIT))
        domain = [("user_id", "=", self.env.uid)]
        if search:
            domain += ["|", "|", ("title", "ilike", search),
                       ("display_text", "ilike", search), ("result_text", "ilike", search)]
        return self.search(domain, limit=limit, order="pinned desc, create_date desc, id desc")._payload()

    @api.model
    def calc_clear_history(self):
        self._check_internal()
        # Les calculs épinglés restent : on les a gardés exprès.
        self.search([("user_id", "=", self.env.uid), ("pinned", "=", False)]).unlink()
        return True

    def calc_delete(self):
        self._check_internal()
        self.filtered(lambda e: e.user_id == self.env.user).unlink()
        return True

    # ------------------------------------------------------------------
    # Taxes
    # ------------------------------------------------------------------
    @api.model
    def _tax_profiles(self):
        """Les combinaisons de taxes de vente de la société courante, lues en
        comptabilité : chaque taxe « groupe » (TPS+TVQ, TPS+TVP…), chaque taxe
        en pourcentage qui n'appartient à aucun groupe (TVH), et **chaque
        composant d'un groupe pris seul** (TVQ seule, TPS seule, TVP seule).
        Le Québec en tête. Sans comptabilité, ou sans taxe, un profil Québec
        par défaut.

        🔴 Jusqu'au 18.0.1.3.0, un composant de groupe était écarté : aucune base
        n'offrait « TVQ seule », alors que la taxe existait bien
        en comptabilité."""
        profiles = []
        if "account.tax" in self.env:
            Tax = self.env["account.tax"].sudo()
            # Société et sociétés parentes : une succursale voit les taxes de la mère.
            taxes = Tax.search([
                ("type_tax_use", "=", "sale"),
                *Tax._check_company_domain(self.env.company),
            ])
            in_group = set()
            #: {id du composant: (taxe, québécois ?)} ; un composant partagé par
            #: plusieurs groupes n'apparaît qu'une fois, et il est québécois dès
            #: qu'UN de ses groupes l'est (la TPS du couple TPS+TVQ).
            singles = {}
            for tax in taxes.filtered(lambda t: t.amount_type == "group"):
                children = tax.children_tax_ids.filtered(
                    lambda c: c.amount_type == "percent" and c.amount > 0)
                if not children:
                    continue
                in_group.update(children.ids)
                components = [(c.tax_group_id.name or c.name, Decimal(str(c.amount)))
                              for c in children]
                qc = _is_qc(components)
                for child in children:
                    kept, was_qc = singles.get(child.id, (child, False))
                    singles[child.id] = (kept, was_qc or qc)
                profiles.append({
                    "key": f"tax:{tax.id}",
                    "name": tax.name,
                    "components": components,
                })
            for tax in taxes.filtered(lambda t: t.amount_type == "percent" and t.amount > 0):
                if tax.id in in_group:
                    continue
                profiles.append({
                    "key": f"tax:{tax.id}",
                    "name": tax.name,
                    "components": [(tax.tax_group_id.name or tax.name, Decimal(str(tax.amount)))],
                })
            for tax, qc in singles.values():
                profiles.append({
                    "key": f"tax:{tax.id}",
                    "name": tax.name,
                    "components": [(tax.tax_group_id.name or tax.name, Decimal(str(tax.amount)))],
                    "qc": qc,
                })

        def is_qc(p):
            return bool(p.get("qc")) or _is_qc(p["components"])

        profiles.sort(key=lambda p: (not is_qc(p), -len(p["components"]),
                                     sum(r for _n, r in p["components"]), p["name"]))
        # Repli seulement quand la comptabilité n'offre AUCUNE taxe : une
        # société ontarienne ne doit pas voir le Québec passer devant sa TVH.
        if not profiles:
            profiles.insert(0, {
                "key": "default_qc",
                "name": _("Quebec (default rates)"),
                "components": list(_DEFAULT_QC),
                "default": True,
            })
        return profiles

    def _tax_label(self, name, total):
        """Le nom comptable, et son taux total s'il ne le porte pas déjà :
        « 14.975% TPS+TVQ » reste tel quel, « TVQ » devient « TVQ (9,975 %) ».

        🔴 La comparaison porte sur des NOMBRES, jamais sur des sous-chaînes :
        auparavant, « Eco-frais 2015 » à 5 % passait pour un nom qui
        porte son taux (le « 5 » de 2015), et « Taxe 1 » à 100 % aussi, parce
        que « 100 ».rstrip("0") vaut « 1 »."""
        total = Decimal(total)
        for brut in _NAME_NUMBER_RE.findall(name or ""):
            if Decimal(brut.replace(",", ".")) == total:
                return name
        return _("%(name)s (%(rate)s)", name=name, rate=self._format_number(total) + "\u00a0%")

    @api.model
    def calc_tax_profiles(self):
        self._check_internal()
        profiles = [{
            "key": p["key"],
            "name": p["name"],
            "label": self._tax_label(p["name"], sum(r for _n, r in p["components"])),
            "default": bool(p.get("default")),
            "manual": False,
            "components": [{"name": n, "rate": float(r)} for n, r in p["components"]],
        } for p in self._tax_profiles()]
        # Le taux libre : pour une taxe qui n'est pas dans la comptabilité (une
        # autre province, un autre pays, un taux d'une autre année). Certaines
        # bases n'offrent qu'un seul profil.
        profiles.append({
            "key": MANUAL_PROFILE,
            "name": _("Manual rate"),
            "label": _("Manual rate"),
            "default": False,
            "manual": True,
            "components": [],
        })
        return profiles

    def _manual_components(self, rates_text):
        """« 9,975 » ou « 5 + 9,975 » : les taux tapés à la main, dans l'ordre.
        Un seul taux par morceau, les morceaux séparés par ``+`` ; la virgule
        reste la décimale. Lève ``CalcError`` sur un morceau qui n'est pas un
        nombre seul, ou sur un taux nul, négatif ou trop grand."""
        # ⚠️ Un seul séparateur, « + » : le texte d'exemple, le message de
        # refus et la fiche n'en nomment pas d'autre, et « 15/2 » passait pour
        # 15 % + 2 %.
        parts = [part for part in (rates_text or "").split("+") if part.strip()]
        if not parts:
            raise calc.CalcError("no_manual_rate")
        if len(parts) > MAX_MANUAL_RATES:
            raise calc.CalcError("too_many_rates", limit=MAX_MANUAL_RATES)
        components = []
        for index, part in enumerate(parts):
            if not _MANUAL_RATE_RE.match(part):
                raise calc.CalcError("bad_rate_part", text=part.strip())
            # 🔴 Lecture indépendante de la LANGUE. Le morceau est déjà validé
            # par ``_MANUAL_RATE_RE`` : il ne porte qu'UN nombre, donc la
            # virgule ne peut y être que la décimale. Avec le séparateur de
            # MILLIERS de la langue, « 9,975 » était REFUSÉ en session anglaise
            # (la virgule y groupe les milliers) et « 0,075 » était accepté à
            # 75 %, sans un mot.
            clean = part.replace("%", "").strip().replace(",", ".")
            try:
                rate = Decimal(clean)
            except InvalidOperation as exc:
                raise calc.CalcError("bad_rate_part", text=part.strip()) from exc
            if rate <= 0 or rate > 100:
                raise calc.CalcError("bad_tax_rate", text=part.strip())
            name = _("Tax") if len(parts) == 1 else _("Tax %(index)s", index=index + 1)
            components.append((name, rate))
        return components

    def _manual_profile(self, rates_text):
        components = self._manual_components(rates_text)
        rates = " + ".join(self._format_number(r) + "\u00a0%" for _n, r in components)
        return {"key": MANUAL_PROFILE,
                "name": _("Manual rate (%(rates)s)", rates=rates),
                "components": components}

    @api.model
    def _tax_breakdown(self, amount, components, direction):
        """``direction`` : ``forward`` (avant taxes → total) ou ``reverse``
        (total → avant taxes, la « taxe inversée »).

        Chaque taxe est arrondie au cent sur le montant avant taxes, comme sur
        une facture. En sens inverse, on cherche, à deux cents près du quotient,
        le montant avant taxes qui, REFACTURÉ, redonne exactement le total.
        🔴 Avant la relecture adverse du 2026-09-22, la base absorbait l'écart
        après coup : les taxes affichées ne correspondaient plus à la base dans
        environ 13 % des totaux (100,10 $ → 87,07 + 4,35 + 8,68, alors que la TVQ
        de 87,07 est 8,69). Quand aucun montant ne redonne ce total, on le dit.

        Rend (base, taxes, total, exact)."""
        amount = _cents(amount)

        def invoice(base):
            taxes = [(n, r, _cents(base * r / 100)) for n, r in components]
            return taxes, base + sum(t for _n, _r, t in taxes)

        if direction != "reverse":
            taxes, total = invoice(amount)
            return amount, taxes, total, True
        factor = 1 + sum(r for _n, r in components) / 100
        guess = _cents(amount / factor)
        for delta in (0, 1, -1, 2, -2):
            base = guess + CENT * delta
            taxes, total = invoice(base)
            if total == amount:
                return base, taxes, total, True
        taxes, total = invoice(guess)
        return guess, taxes, total, False

    @api.model
    def calc_tax(self, amount_text, profile_key, direction="reverse", record=False,
                 res_model=False, res_id=False, rates_text=""):
        self._check_internal()
        try:
            amount = calc.parse_amount(amount_text, self._thousands_sep(),
                                        variables=self._variables())
            # Une clé inconnue REFUSE, ici comme dans l'opération : auparavant,
            # « tax:999999 » annonçait « 14.975% TPS+TVQ ».
            profile = (self._manual_profile(rates_text) if profile_key == MANUAL_PROFILE
                       else self._pick_profile(self._tax_profiles(), profile_key))
        except calc.CalcError as err:
            return {"ok": False, "error": self._error_message(err)}
        if direction not in ("forward", "reverse"):
            direction = "reverse"
        base, taxes, total, exact = self._tax_breakdown(amount, profile["components"], direction)
        rate_text = lambda r: self._format_number(r) + "\u00a0%"
        lines = [{"name": n, "rate": rate_text(r), "amount": self._format_money(t)}
                 for n, r, t in taxes]
        detail = " ; ".join(f"{l['name']} ({l['rate']}) {l['amount']}" for l in lines)
        if direction == "reverse":
            display = _("Reverse tax (%(profile)s) on %(total)s: before taxes %(base)s; %(detail)s",
                        profile=profile["name"], total=self._format_money(total),
                        base=self._format_money(base), detail=detail)
            result, result_text = base, self._format_money(base)
        else:
            display = _("Taxes (%(profile)s) on %(base)s: %(detail)s; total %(total)s",
                        profile=profile["name"], base=self._format_money(base),
                        detail=detail, total=self._format_money(total))
            result, result_text = total, self._format_money(total)
        payload = {
            "ok": True,
            "profile": profile["name"],
            "direction": direction,
            "base": self._format_money(base),
            "total": self._format_money(total),
            "taxes": lines,
            # Taxe inversée sans montant exact : le plus proche, et son vrai total.
            "inexact": "" if exact else self._format_money(total),
            "display": display,
            "result_text": result_text,
            "raw": _plain(result),
        }
        if record:
            entry = self.create({
                "expression": (amount_text or "")[:calc.MAX_LENGTH] or "0",
                "display_text": display,
                "result": float(result),
                "result_text": result_text,
                "mode": "tax",
                **self._origin_vals(res_model, res_id),
            })
            payload["entry"] = entry._payload()[0]
        return payload

    # ------------------------------------------------------------------
    # Pourcentages et marges
    # ------------------------------------------------------------------
    @api.model
    def calc_percent(self, kind, a_text, b_text, record=False, res_model=False, res_id=False):
        """``change`` : variation de A à B. ``margin`` : coût A, prix B.
        ``discount`` : prix A, remise B %. ``portion`` : A est quel % de B."""
        self._check_internal()
        sep = self._thousands_sep()
        try:
            variables = self._variables()
            a = calc.parse_amount(a_text, sep, variables)
            b = calc.parse_amount(b_text, sep, variables)
        except calc.CalcError as err:
            return {"ok": False, "error": self._error_message(err)}
        num = self._format_number
        pct = lambda v: num(v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)) + "\u00a0%"
        rows = []
        if kind == "change":
            if a == 0:
                return {"ok": False, "error": _("Division by zero.")}
            value = (b - a) / a * 100
            rows = [(_("Change"), pct(value)), (_("Difference"), num(b - a))]
            display = _("Change from %(a)s to %(b)s", a=num(a), b=num(b))
        elif kind == "margin":
            if a == 0 or b == 0:
                return {"ok": False, "error": _("Division by zero.")}
            value = (b - a) / b * 100
            rows = [(_("Margin (on price)"), pct(value)),
                    (_("Markup (on cost)"), pct((b - a) / a * 100)),
                    (_("Profit"), num(b - a))]
            display = _("Margin: cost %(a)s, price %(b)s", a=num(a), b=num(b))
        elif kind == "discount":
            value = a - a * b / 100
            rows = [(_("Net price"), num(value)), (_("Discount"), num(a * b / 100))]
            display = _("Discount of %(b)s %% on %(a)s", a=num(a), b=num(b))
        elif kind == "portion":
            if b == 0:
                return {"ok": False, "error": _("Division by zero.")}
            value = a / b * 100
            rows = [(_("Share"), pct(value))]
            display = _("%(a)s out of %(b)s", a=num(a), b=num(b))
        else:
            return {"ok": False, "error": _("Unknown calculation.")}
        result_text = rows[0][1]
        payload = {
            "ok": True,
            "rows": [{"label": l, "value": v} for l, v in rows],
            "display": display,
            "result_text": result_text,
            "raw": _plain(value),
        }
        if record:
            entry = self.create({
                "expression": f"{a_text} ; {b_text}"[:calc.MAX_LENGTH],
                "display_text": display + " : " + " ; ".join(f"{l} {v}" for l, v in rows[1:])
                if len(rows) > 1 else display,
                "result": float(value),
                "result_text": result_text,
                "mode": "percent",
                **self._origin_vals(res_model, res_id),
            })
            payload["entry"] = entry._payload()[0]
        return payload

    # ------------------------------------------------------------------
    # Colonne collée
    # ------------------------------------------------------------------
    @api.model
    def calc_column(self, text, record=False, res_model=False, res_id=False):
        self._check_internal()
        values, skipped = calc.parse_column(text or "", self._thousands_sep())
        if not values:
            return {"ok": False, "error": _("No number found in the pasted text.")}
        total = sum(values)
        num = self._format_number
        rows = [
            (_("Sum"), num(total)),
            (_("Average"), num(total / len(values))),
            (_("Count"), str(len(values))),
            (_("Minimum"), num(min(values))),
            (_("Maximum"), num(max(values))),
        ]
        display = _("Sum of a pasted column (%(count)s values, %(skipped)s ignored)",
                    count=len(values), skipped=skipped)
        payload = {
            "ok": True,
            "rows": [{"label": l, "value": v} for l, v in rows],
            "skipped": skipped,
            "display": display,
            "result_text": num(total),
            "raw": _plain(total),
        }
        if record:
            entry = self.create({
                "expression": (text or "")[:calc.MAX_LENGTH],
                "display_text": display,
                "result": float(total),
                "result_text": num(total),
                "mode": "column",
                **self._origin_vals(res_model, res_id),
            })
            payload["entry"] = entry._payload()[0]
        return payload

    # ------------------------------------------------------------------
    # Chatter
    # ------------------------------------------------------------------
    def action_open_post_wizard(self):
        self._check_internal()
        entries = self.filtered(lambda e: e.user_id == self.env.user)
        if not entries:
            raise UserError(_("Select at least one of your calculations."))
        ctx = {"default_entry_ids": [(6, 0, entries.ids)]}
        origin = entries.filtered("res_model")[:1]
        target_model = self.env.context.get("bf_calc_res_model") or origin.res_model
        target_id = self.env.context.get("bf_calc_res_id") or origin.res_id
        if target_model and target_id:
            ctx.update(bf_calc_res_model=target_model, bf_calc_res_id=target_id)
        return {
            "type": "ir.actions.act_window",
            "name": _("Post to a chatter"),
            "res_model": "bf.calculator.post",
            "view_mode": "form",
            # Sans `views`, le doAction du panneau (appel ORM, pas un bouton)
            # plante dans _preprocessAction.
            "views": [(False, "form")],
            "target": "new",
            "context": ctx,
        }

    # ------------------------------------------------------------------
    # Purge
    # ------------------------------------------------------------------
    @api.model
    def _cron_purge_history(self):
        raw = self.env["ir.config_parameter"].sudo().get_param(
            HISTORY_DAYS_PARAM, DEFAULT_HISTORY_DAYS)
        try:
            days = int(raw)
        except (TypeError, ValueError):
            _logger.warning("%s=%r n'est pas un entier : purge sautée", HISTORY_DAYS_PARAM, raw)
            return
        if days <= 0:
            return
        limit = fields.Datetime.now() - timedelta(days=days)
        old = self.sudo().search([("create_date", "<", limit), ("pinned", "=", False)])
        if old:
            _logger.info("Calculatrice : %s ligne(s) de plus de %s jours purgée(s)", len(old), days)
            old.unlink()
