"""Le bien du carnet cite le bien de l'exploitation.

⚠️ Une citation, pas une fusion. Les deux lectures du même bien ne partagent
que le nom et la société : 23 champs propres au carnet, 27 propres à
l'exploitation, et aucune des deux ne contient l'autre. Recopier un champ d'un
côté vers l'autre créerait précisément le deuxième registre que ce pont évite.

⚠️ Le sens de la citation compte. C'est le carnet qui pointe vers
l'exploitation, parce que le carnet est un document daté qu'un professionnel
indépendant établit et qui, une fois établi, cite l'état du parc à ce
moment-là. L'exploitation, elle, continue de vivre : elle remplace, elle met au
rebut, elle rachète.
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

DRIFT_STATES = [
    ("scrapped", "Mis au rebut à l'exploitation"),
    ("archived", "Archivé à l'exploitation"),
]


class BfPropertyMaintenanceItem(models.Model):
    _inherit = "bf.property.maintenance.item"

    equipment_id = fields.Many2one(
        "maintenance.equipment",
        string="Bien à l'exploitation",
        # ⚠️ `restrict` et non `set null` : un carnet est un document
        # réglementaire daté. Effacer sa citation en silence parce que
        # quelqu'un a supprimé une fiche d'équipement lui ferait perdre une
        # trace sans que personne l'apprenne. Le chemin normal du retrait
        # n'est pas la suppression, c'est la mise au rebut ou l'archivage —
        # et ces deux-là, le module les signale.
        ondelete="restrict",
        domain="[('bf_building_id', '=', building_id)]",
        help="L'équipement que l'exploitation tient pour ce même bien. La "
             "citation ne déplace aucune donnée : elle sert à voir, depuis le "
             "carnet, ce que l'exploitation en a fait depuis.",
    )
    equipment_drift = fields.Selection(
        DRIFT_STATES,
        string="Écart avec l'exploitation",
        compute="_compute_equipment_drift",
        store=True,
        help="Signalé quand l'exploitation a retiré le bien du service et que "
             "le carnet n'a pas encore noté de travaux réalisés. L'art. 4 du "
             "règlement veut, à la mise à jour annuelle, ce qui n'a pas été "
             "fait ET pourquoi : la raison vient du conseil, jamais d'ici.",
    )


    # ── Ce que l'exploitation a sauté (r. 8.01, art. 4) ──

    # ⚠️ L'aide d'origine dit « la raison vient du conseil ». Elle reste vraie,
    # et elle se précise ici : quand l'exploitation déclare un entretien
    # cédulé non effectué, la raison vient de la personne qui l'a constaté sur
    # place. Elle n'est jamais calculée, et elle ne remplace jamais un texte
    # déjà écrit — elle comble un silence.
    not_done_reason = fields.Char(
        help="r. 8.01, art. 4 : lors de la mise à jour annuelle, si des "
             "travaux requis ou prévus n'ont pas été effectués, le carnet le "
             "mentionne ET indique pourquoi. Le module ne juge pas qu'un "
             "travail aurait dû l'être. Deux personnes peuvent l'écrire : le "
             "conseil, et le concierge qui déclare à l'exploitation un "
             "entretien cédulé non effectué. La seconde ne remplace jamais la "
             "première ; elle écrit là où le carnet se taisait, et le relevé "
             "ci-dessous porte toutes les occurrences.",
    )
    bf_skipped_count = fields.Integer(
        string="Entretiens cédulés non effectués",
        compute="_compute_bf_skipped",
        help="Le nombre d'entretiens préventifs que l'exploitation a déclarés "
             "non effectués sur le bien cité. L'art. 4 en veut la mention ET "
             "la raison à chaque mise à jour annuelle.",
    )
    bf_skipped_summary = fields.Text(
        string="Relevé des entretiens non effectués",
        compute="_compute_bf_skipped",
        help="Une ligne par occurrence sautée, avec sa date et sa raison. Le "
             "champ « Non effectué, parce que » n'en tient qu'une : ce relevé "
             "existe pour que les autres ne se perdent pas.",
    )

    @api.depends("equipment_id")
    @api.depends_context("lang")
    def _compute_bf_skipped(self):
        """sudo : le carnet se lit par un gestionnaire de copropriété, qui n'a
        aucun droit sur les billets d'exploitation. Sans `sudo`, le relevé
        lèverait un refus d'accès sur un carnet qu'il a le droit d'ouvrir.
        Rien n'est ouvert pour autant : le relevé est un texte, et il ne porte
        que les travaux du bien que ce carnet cite déjà.
        """
        # ⚠️ UNE recherche pour tout le jeu, pas une par bien. Un carnet porte
        # deux cents biens et la colonne s'affiche par défaut : chercher bien
        # par bien fait deux cents requêtes pour rendre une page de liste.
        # Mesuré : 11 requêtes pour dix biens contre 2 pour un seul.
        Request = self.env["maintenance.request"].sudo()
        equipment = self.mapped("equipment_id")
        by_equipment = {}
        if equipment:
            for work in Request.search(
                [
                    ("equipment_id", "in", equipment.ids),
                    ("bf_not_done_reason", "!=", False),
                ],
                order="bf_not_done_date desc, id desc",
            ):
                by_equipment.setdefault(work.equipment_id.id, []).append(work)
        for item in self:
            if not item.equipment_id:
                item.bf_skipped_count = 0
                item.bf_skipped_summary = False
                continue
            skipped = by_equipment.get(item.equipment_id.id, [])
            item.bf_skipped_count = len(skipped)
            # 🔴 Le gabarit se traduit HORS de l'expression génératrice : Odoo
            # cherche la langue dans le cadre qui appelle `_()`, et une genexpr
            # n'a pas `self`. Le relevé restait en français.
            row = _("%(date)s, %(work)s : %(reason)s")
            item.bf_skipped_summary = "\n".join(
                row % {
                    "date": work.bf_not_done_date or work.request_date,
                    "work": work.name or "",
                    "reason": work.bf_not_done_reason,
                }
                for work in skipped
            ) or False

    @api.depends(
        "equipment_id",
        "equipment_id.scrap_date",
        "equipment_id.active",
        "done_date",
    )
    def _compute_equipment_drift(self):
        for item in self:
            equipment = item.equipment_id
            # Des travaux notés au carnet éteignent l'écart : le conseil a fait
            # sa mise à jour, et le carnet dit à nouveau ce qui est.
            if not equipment or item.done_date:
                item.equipment_drift = False
            elif equipment.scrap_date:
                item.equipment_drift = "scrapped"
            elif not equipment.active:
                item.equipment_drift = "archived"
            else:
                item.equipment_drift = False

    @api.constrains("equipment_id", "building_id")
    def _check_equipment_building(self):
        for item in self:
            equipment = item.equipment_id
            if not equipment or not equipment.bf_building_id:
                continue
            if item.building_id and equipment.bf_building_id != item.building_id:
                raise ValidationError(
                    _(
                        "« %(item)s » est porté à l'immeuble %(building)s et "
                        "cite un équipement rattaché à %(other)s.",
                        item=item.name or "",
                        building=item.building_id.display_name,
                        other=equipment.bf_building_id.display_name,
                    )
                )

    @api.onchange("equipment_id")
    def _onchange_equipment_id(self):
        """Citer un équipement dit dans quel immeuble le bien se trouve."""
        for item in self:
            if item.equipment_id.bf_building_id and not item.building_id:
                item.building_id = item.equipment_id.bf_building_id
