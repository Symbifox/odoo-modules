"""Le travail à exécuter sait de quelle demande d'occupant il vient.

Le lien sert à deux choses et à rien d'autre : remonter au fil avec l'occupant
depuis l'atelier, et voir, au bout, ce qu'une demande a réellement donné comme
travaux. Il ne recopie aucune donnée de la demande — surtout pas la lecture de
l'art. 1064, qui reste là où elle se calcule.

⚠️ **Une exception, et elle est nommée** : la nature des travaux. Le même mot du
règlement sépare les deux côtés — `work_type` sur la demande répartit la dépense
(art. 1064 C.c.Q.), `bf_repair_scope` sur le travail décide de ce qui remonte au
carnet (r. 8.01, art. 2 al. 2, par. 3° contre art. 3 al. 2). Ce n'est pas la même
règle, mais c'est le même fait, et le faire redemander à la personne qui ouvre le
travail lui ferait donner deux fois la même réponse — avec le droit de se
contredire. Le préremplissage n'est donc pas une recopie de confort : il évite
deux qualifications divergentes du même travail dans un dossier réglementaire.

⚠️ Il **préremplit** et n'impose rien. Un répartiteur qui corrige la nature au
vu du chantier a le dernier mot, et une demande requalifiée après coup ne
reprend pas ce qu'il a écrit. C'est l'inverse de l'équipe, qui suit la demande :
une équipe se réaffecte, une qualification réglementaire se constate.
"""
from odoo import api, fields, models

# `work_type` de la demande → `bf_repair_scope` du travail. « À déterminer »
# n'est pas une réponse et ne remplit rien : le silence du carnet vaut mieux
# qu'une nature devinée.
SCOPE_FROM_WORK_TYPE = {
    "maintenance": "routine",
    "major": "major",
}


class MaintenanceRequest(models.Model):
    _inherit = "maintenance.request"

    @api.model_create_multi
    def create(self, vals_list):
        """🔴 Un champ calculé qui porte AUSSI un `default` ne calcule pas.

        `maintenance_team_id` a `default=_get_default_team_id`, qui rend la
        première équipe venue. À la création, Odoo applique ce défaut, le champ
        cesse d'être « à calculer », et le calcul n'est jamais joué : le
        travail part chez une équipe au hasard pendant que l'acheminement a
        l'air branché. Mesuré, et invisible autrement —
        la seule trace est une équipe plausible qui n'est pas la bonne.

        L'acheminement se pose donc AVANT `super()`, et seulement là où rien
        n'a été demandé explicitement.
        """
        for vals in vals_list:
            if vals.get("maintenance_team_id") or not vals.get("bf_request_id"):
                continue
            equipment = self.env["maintenance.equipment"].browse(
                vals.get("equipment_id") or []
            )
            if equipment.maintenance_team_id:
                # L'équipement tranche, et le module d'origine le fera.
                continue
            # sudo : un technicien n'a aucun droit sur les demandes
            # d'occupants, et poser une équipe ne doit pas devenir un refus
            # d'accès sur un billet qu'il a le droit d'ouvrir.
            team = (
                self.env["bf.property.request"]
                .sudo()
                .browse(vals["bf_request_id"])
                .maintenance_team_id
            )
            if team:
                vals["maintenance_team_id"] = team.id
        return super().create(vals_list)

    bf_request_id = fields.Many2one(
        "bf.property.request",
        string="Demande de l'occupant",
        # ⚠️ `restrict` : une demande porte un fil avec une personne. L'effacer
        # sous un travail en cours ferait perdre le pourquoi de ce travail.
        ondelete="restrict",
        index=True,
        help="La demande d'où vient ce travail, lorsqu'il en vient d'une. Un "
             "préventif cédulé n'en a pas.",
    )

    # Les deux surcharges répètent le `depends` du parent par lisibilité, non
    # par nécessité : mesuré, Odoo CUMULE les `depends`
    # de la chaîne d'héritage. `field_depends` rend d'ailleurs les doublons
    # tels quels — ('company_id', 'equipment_id', 'bf_request_id',
    # 'company_id', 'equipment_id').
    @api.depends("equipment_id", "bf_request_id")
    def _compute_bf_building_id(self):
        super()._compute_bf_building_id()
        for work in self:
            if not work.bf_building_id:
                work.bf_building_id = work.bf_request_id.building_id

    @api.depends("company_id", "equipment_id", "bf_request_id")
    def _compute_maintenance_team_id(self):
        """L'équipe vient de l'équipement, sinon de la demande.

        🔴 `maintenance_team_id` porte un `default=_get_default_team_id` qui
        rend « la première équipe venue ». Une garde « ne remplis que si c'est
        vide » ne se déclenche donc JAMAIS à la création : le défaut a déjà
        rempli le champ, et l'acheminement paraît fait sans l'être. Le module
        d'origine tranche déjà ainsi — la valeur dérivée de l'équipement écrase
        ce qui s'y trouve — et on suit sa convention plutôt que d'en inventer
        une deuxième.

        ⚠️ Le calcul ne dépend PAS de l'équipe de la demande, seulement du lien
        vers elle : réaffecter une équipe sur la demande ne doit pas reprendre
        les travaux qu'un répartiteur a confiés à un spécialiste.
        """
        super()._compute_maintenance_team_id()
        for work in self:
            if work.equipment_id.maintenance_team_id:
                continue
            team = work.bf_request_id.maintenance_team_id
            if team:
                work.maintenance_team_id = team

    @api.depends("maintenance_type", "bf_request_id.work_type")
    def _compute_bf_repair_scope(self):
        """La nature vient de la demande, tant que personne ne l'a écrite.

        ⚠️ `sudo` : un technicien n'a aucun droit sur les demandes d'occupants,
        et lire la nature des travaux ne doit pas se muer en refus d'accès sur
        un billet qu'il a le droit d'ouvrir. Même motif que l'acheminement
        au-dessus.
        """
        super()._compute_bf_repair_scope()
        for work in self:
            if work.maintenance_type != "corrective" or work.bf_repair_scope:
                continue
            work.bf_repair_scope = SCOPE_FROM_WORK_TYPE.get(
                work.bf_request_id.sudo().work_type
            ) or False
