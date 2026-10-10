# Part of Healthy Fox. See LICENSE file for full copyright and licensing details.
"""Le nom d'une fiche santé, pour qui ne peut pas la lire : « Fiche santé privée ».

Odoo lit certains noms en superutilisateur. En mode debug, que tout interne peut
ouvrir, l'erreur d'accès d'une règle liste les fiches refusées par leur nom
(``ir.rule._make_access_error`` : ``records[:6].sudo().display_name``). Un membre
qui lisait un id deviné apprenait ainsi le nom du médicament d'une autre personne.
Même chose pour un Many2one ou un champ Reference lus en sudo.

``sudo()`` garde ``env.uid`` : le calcul sait encore qui lit, et demande à la règle
du modèle, telle qu'elle est, si cette personne peut lire la fiche. Le
superutilisateur (tâches planifiées) lit le vrai nom.
"""
from odoo import SUPERUSER_ID, _, api, models

#: Les modèles à fiches privées (règle globale ``create_uid``), et leur sorte.
MODELES_PRIVES = (
    ("health.condition", models.Model),
    ("health.food", models.Model),
    ("health.lab.test", models.Model),
    ("health.meal.log", models.Model),
    ("health.medication", models.Model),
    ("health.medication.log", models.Model),
    ("health.mood.activity", models.Model),
    ("health.mood.entry", models.Model),
    ("health.mood.settings", models.Model),
    ("health.nutrition.goal", models.Model),
    ("health.reduction.step", models.Model),
    ("health.screening", models.Model),
    ("health.substance.log", models.Model),
    ("health.symptom.log", models.Model),
    ("health.vital", models.Model),
    ("health.workout", models.Model),
    ("health.daily.log.wizard", models.TransientModel),
    ("health.daily.log.wizard.med.line", models.TransientModel),
    ("health.daily.log.wizard.meal.line", models.TransientModel),
    ("health.mood.report.wizard", models.TransientModel),
)


class HealthPrivateName(models.AbstractModel):
    _name = "bf.health.private.name"
    _description = "Healthy Fox : nom neutre pour qui ne lit pas la fiche"

    @api.depends_context("uid")
    def _compute_display_name(self):
        super()._compute_display_name()
        if self.env.uid == SUPERUSER_ID:
            return
        reelles = self.filtered(lambda rec: isinstance(rec.id, int))
        if not reelles:
            return
        lisibles = set(reelles.with_env(reelles.env(su=False))._filtered_access("read").ids)
        for rec in reelles:
            if rec.id not in lisibles:
                rec.display_name = _("Fiche santé privée")


#: Définis dans ``wizard/`` : étendus par ``wizard/nom_prive.py``, une fois déclarés.
MODELES_DE_L_ASSISTANT = (
    "health.daily.log.wizard",
    "health.daily.log.wizard.med.line",
    "health.daily.log.wizard.meal.line",
)


def donner_le_nom_prive(modeles, module):
    """Chaque modèle privé reçoit le mixin. L'humeur garde son propre nom, déjà
    neutre : sa méthode passe avant celle du mixin."""
    for modele, sorte in MODELES_PRIVES:
        if modele in modeles:
            type(
                "NomPrive_" + modele.replace(".", "_"),
                (sorte,),
                {"__module__": module, "_name": modele, "_inherit": [modele, "bf.health.private.name"]},
            )


donner_le_nom_prive({m for m, _s in MODELES_PRIVES} - set(MODELES_DE_L_ASSISTANT), __name__)
