"""Tuile « Budget » du tableau de bord Symbifox.

Même patron d'extension que les autres sections : une clé de plus dans
`get_dashboard_data()`, calculée par une aide défensive, dans un point de
reprise. Le tableau de bord ne doit jamais casser parce qu'une section échoue,
et une exception laisse la transaction avortée pour toutes les suivantes.

🔴 **La tuile est pour qui lit les budgets, et personne d'autre.**
`bf.budget` n'est ouvert qu'à `bf_budget.group_bf_budget_user` (lecture seule)
et à son gestionnaire. Sans garde, chaque chargement de l'accueil par un
employé ordinaire lèverait `AccessError`, que l'agrégateur prendrait pour une
panne : la tuile dirait « Données non disponibles » et chaque visite laisserait
une trace au journal. Ne pas avoir le droit n'est pas une panne, donc : pas de
tuile, rien au journal.

⚠️ Les montants, eux, se calculent en `sudo` DANS le socle (`_read_actual`),
parce qu'un lecteur de budget n'a pas forcément accès au grand livre. C'est le
dessein du module, et la tuile n'élargit rien : elle montre à un lecteur de
budget ce que l'écran Budgets lui montre déjà.

**Ce que la tuile dit, et ce qu'elle ne dit pas.** Un seul budget est cité, le
plus récent qui soit ouvert, parce qu'un pourcentage qui mélangerait des
exercices et des sens (dépense, revenu) ne voudrait rien dire. Le compte de
lignes signalées, lui, porte sur TOUS les budgets ouverts de la société : c'est
le chiffre sur lequel on agit, et en manquer un parce qu'il est sur un autre
budget serait pire que de n'avoir aucune tuile.
"""

import logging

from odoo import api, models
from odoo.exceptions import AccessError
from odoo.tools.misc import format_amount

_logger = logging.getLogger(__name__)

#: Les modèles que la tuile lit. Le droit de lecture décide : les règles
#: d'enregistrement du socle bornent déjà à la société, et aucune lecture
#: n'échappe ici aux règles (le `sudo` du socle est interne à ses calculs).
BUDGET_MODELS_READ = ("bf.budget", "bf.budget.line")

#: 🔴 Au-delà de ce nombre de lignes ouvertes, la tuile cesse de compter les
#: alertes de TOUS les budgets et se borne à celui qu'elle cite. Le socle lit le
#: réel ligne par ligne (un `_read_group` par ligne, mesuré à ~3 requêtes SQL
#: chacune) : à 192 lignes, l'accueil prenait 4 secondes de plus, à chaque
#: visite, pour chaque lecteur de budget. Une tuile n'a pas le droit de coûter
#: ça. Quand la borne est franchie, la tuile le DIT plutôt que de rendre un
#: chiffre partiel sans le signaler.
MAX_LINES_SCANNED = 60


class BfDashboard(models.AbstractModel):
    _inherit = "bf.dashboard"
    # ⚠️ `AbstractModel` obligatoire : `bf.dashboard` est abstrait (aucune
    # table), et `BaseModel._build_model_check_base` lève `TypeError` dès qu'une
    # classe concrète l'étend. Le registre ne charge alors plus du tout.

    @api.model
    def get_dashboard_data(self):
        data = super().get_dashboard_data()
        try:
            # Le point de reprise protège les sections calculées APRÈS celle-ci.
            with self.env.cr.savepoint():
                data["budget"] = self._get_budget_summary()
        except Exception:  # noqa: BLE001 - une section cassée n'emporte pas la page
            _logger.exception("Erreur au chargement du sommaire Budget")
            data["budget"] = None
            # Même convention que les collecteurs de `bf_home` : une section qui
            # a levé est nommée dans `failed`, une section muette ne l'est pas.
            if isinstance(data.get("failed"), dict):
                data["failed"]["budget"] = True
        return data

    @api.model
    def _budget_may_read(self):
        """Vrai quand l'usager lit les budgets et leurs lignes."""
        if self.env.su:
            return True
        return all(
            model in self.env and self.env[model].browse().has_access("read")
            for model in BUDGET_MODELS_READ
        )

    @api.model
    def _get_budget_summary(self):
        if not self._budget_may_read():
            return None
        try:
            Budget = self.env["bf.budget"]
            # 🔴 Les sociétés ACTIVES, pas la seule société par défaut. Sur une
            # base multi-sociétés, avec `env.company`, un dépassement sur une
            # autre société cochée dans le sélecteur restait invisible à l'accueil
            # pendant que le digest du matin le signalait. Deux surfaces qui
            # disent le contraire sur les mêmes données, c'est la surface qui
            # ment qu'on croit.
            companies = self.env.companies or self.env.company
            # `_order` du modèle : date_start desc, id desc. Le premier est donc
            # le plus récent, et c'est celui qu'on cite.
            opened = Budget.search(
                [("state", "=", "open"), ("company_id", "in", companies.ids)]
            )
            if not opened:
                return None
            # Le budget dont la personne répond passe devant : c'est le sien
            # qu'elle vient voir. À défaut, le plus récent.
            mine = opened.filtered(lambda b: b.user_id == self.env.user)
            shown = (mine or opened)[0]
            planned = shown.amount_planned
            committed = shown.amount_committed
            currency = shown.currency_id or self.env.company.currency_id
            # Le compte d'alertes porte sur TOUS les budgets ouverts : manquer
            # un dépassement parce qu'il est sur l'autre budget serait pire que
            # pas de tuile. Mais il se paie en requêtes, d'où la borne.
            total_lines = sum(opened.mapped("line_count"))
            scanned = opened if total_lines <= MAX_LINES_SCANNED else shown
            return {
                "name": shown.display_name,
                "kind": shown.budget_type,
                # Un budget sans montant prévu ne porte aucun pourcentage
                # défendable : on montre alors la dérive seule.
                "consumed_display": (
                    "%d %%" % round(committed / planned * 100.0) if planned else None
                ),
                "alert_lines": sum(scanned.mapped("alert_line_count")),
                # Le rouge de la tuile suit le budget CITÉ : une bordure rouge
                # au-dessus du nom et du pourcentage d'un autre budget se lit
                # comme « celui-ci dépasse », ce qui serait faux.
                "shown_alert_lines": shown.alert_line_count,
                "partial_count": scanned != opened,
                "budget_count": len(opened),
                # Engagé moins théorique, formaté : positif sur une dépense =
                # on dépense plus vite que le plan à ce jour. Formaté ici, par
                # `format_amount`, parce que « +1900$ » n'est pas un montant en
                # français.
                "drift": round(committed - shown.amount_theoretical),
                "drift_display": format_amount(
                    self.env, committed - shown.amount_theoretical, currency
                ),
                "currency": currency.symbol or "",
            }
        except AccessError:
            # Une règle d'enregistrement propre au locataire peut encore refuser
            # une lecture que les ACL laissaient passer. Et le calcul du réel
            # touche `account.analytic.line` sans `sudo` sur une ligne bornée à
            # l'analytique : pas de tuile, pas de trace.
            _logger.debug(
                "Sommaire Budget : lecture refusée à %s", self.env.user.login
            )
            return None
