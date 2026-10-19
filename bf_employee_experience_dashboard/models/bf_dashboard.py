"""Tuile « Expérience employé » du tableau de bord Blue Fox.

Même patron d'extension que les autres sections : une clé de plus dans
`get_dashboard_data()`, calculée par une aide défensive. Le tableau de bord ne
doit jamais casser parce qu'une section échoue.

🔴 **La tuile est un indicateur de l'administration des avantages**.
Le calcul de `bf.ex.benefit` parcourt les règles d'admissibilité, les droits et
les usages. Un employé ordinaire lit le catalogue, mais pas les règles (aucune
ACL), et seulement SES droits et SES usages (règles d'enregistrement) : chaque
chargement de l'accueil levait donc `AccessError` sur `bf.ex.eligibility.rule`,
journalisée avec sa trace. Et sans cette erreur, le chiffre aurait été faux :
un taux d'adhésion calculé sur ses seules lignes. La tuile ne se calcule donc
que pour qui voit tout le registre, c'est-à-dire `hr.group_hr_user`, et
seulement si chaque modèle lu est lisible. Les autres reçoivent `None`, sans
rien journaliser : ne pas avoir le droit n'est pas une panne.

La garde est ici, dans le pont, et non dans `bf_home` : elle doit tenir quelle
que soit la garde générique de l'agrégateur.
"""

import logging

from odoo import api, models
from odoo.exceptions import AccessError

_logger = logging.getLogger(__name__)

#: Le groupe dont les règles d'enregistrement ouvrent TOUT le registre
#: (`rule_entitlement_hr`, `rule_usage_hr`). En dessous, les compteurs ne
#: porteraient que les lignes de la personne.
EX_ADMIN_GROUP = "hr.group_hr_user"

#: Les modèles que le calcul de la tuile lit, directement ou par les champs
#: calculés de `bf.ex.benefit`.
EX_MODELS_READ = (
    "bf.ex.benefit",
    "bf.ex.eligibility.rule",
    "bf.ex.entitlement",
    "bf.ex.usage",
)


class BfDashboard(models.AbstractModel):
    _inherit = "bf.dashboard"
    # ⚠️ `AbstractModel` obligatoire : `bf.dashboard` est abstrait (aucune
    # table), et `BaseModel._build_model_check_base` lève `TypeError` dès qu'une
    # classe concrète l'étend. Le registre ne charge alors plus du tout.

    @api.model
    def get_dashboard_data(self):
        data = super().get_dashboard_data()
        try:
            # Le point de reprise protège les sections calculées APRÈS celle-ci :
            # une exception levée dans une requête laisse la transaction avortée.
            with self.env.cr.savepoint():
                data["employee_experience"] = self._get_ex_summary()
        except Exception:  # noqa: BLE001 - une section cassée n'emporte pas la page
            _logger.exception("Erreur au chargement du sommaire Expérience employé")
            data["employee_experience"] = None
            # Même convention que les collecteurs de `bf_home` : une section qui
            # a levé est nommée dans `failed`, une section muette ne l'est pas.
            if isinstance(data.get("failed"), dict):
                data["failed"]["employee_experience"] = True
        return data

    @api.model
    def _ex_may_read(self):
        """Vrai quand l'utilisateur voit tout le registre que la tuile résume."""
        if self.env.su:
            return True
        if not self.env.user.has_group(EX_ADMIN_GROUP):
            return False
        return all(
            model in self.env and self.env[model].browse().has_access("read")
            for model in EX_MODELS_READ
        )

    @api.model
    def _get_ex_summary(self):
        if not self._ex_may_read():
            return None
        try:
            Benefit = self.env["bf.ex.benefit"]
            benefits = Benefit.search([("company_id", "=", self.env.company.id)])
            if not benefits:
                return None
            with_rights = benefits.filtered(lambda b: b.entitled_count)
            # `uptake_rate` vaut 0 à 100 (pas une fraction) : « 50 % » s'écrit 50.
            uptake = (
                sum(with_rights.mapped("uptake_rate")) / len(with_rights)
                if with_rights else 0.0
            )
            return {
                "benefit_count": len(benefits),
                "uptake_display": "%d %%" % round(uptake),
                "unused_count": len(benefits.filtered("unused")),
                "annual_cost": round(sum(benefits.mapped("annual_cost"))),
                "currency": self.env.company.currency_id.symbol or "",
            }
        except AccessError:
            # Une règle d'accès propre au locataire peut encore refuser une
            # lecture que les ACL laissaient passer : pas de tuile, pas de trace.
            _logger.debug("Sommaire Expérience employé : lecture refusée à %s",
                          self.env.user.login)
            return None
