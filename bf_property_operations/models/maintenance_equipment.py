"""Où se trouve un équipement, dans un immeuble.

`maintenance.equipment` sait le fournisseur, le modèle, le numéro de série, la
garantie, l'équipe et le MTBF. Il ne sait pas où l'appareil est posé : son champ
`location` est un Char, « Used in location », qui sert à écrire « 3e étage »
dans un atelier. Un concierge qu'on envoie réparer une chaudière a besoin de
l'immeuble, et parfois de la fraction.

⚠️ **Les constats se lisent en `sudo()`, et c'est délibéré.** Les champs du bâti
pointent vers des modèles de la suite dont un technicien n'a pas la lecture. Un
contrôle de cohérence qui traverserait ces pointeurs avec les droits de
l'utilisateur transformerait une règle de données en refus d'accès, sur un
enregistrement que l'utilisateur a pourtant le droit d'écrire. Le `sudo()` ne
donne rien à personne : il sert à valider, jamais à afficher.

⚠️ **Aucun droit n'est élargi ici.** Les champs sont posés sur l'écran derrière
le groupe Consultation de la suite plutôt que par `groups=` sur le champ
lui-même : un `groups=` de champ ferait échouer les contrôles de cohérence et
les duplications d'un utilisateur hors groupe, alors que la barrière utile — le
registre des copropriétaires, qui vit sur la fraction — est déjà tenue par les
droits d'accès de `bf_property_core`.
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class MaintenanceEquipment(models.Model):
    _inherit = "maintenance.equipment"

    bf_building_id = fields.Many2one(
        "bf.property.building",
        string="Immeuble",
        index=True,
        ondelete="restrict",
        help="L'immeuble où l'équipement est installé. C'est ce rattachement "
             "qui permet d'acheminer un travail vers l'équipe qui a "
             "l'immeuble, et de compter l'arriéré par immeuble.",
    )
    bf_unit_id = fields.Many2one(
        "bf.property.unit",
        string="Fraction",
        ondelete="restrict",
        help="La partie privative où l'équipement est installé, lorsqu'il y en "
             "a une. Un équipement est dans une fraction ou dans une partie "
             "commune, jamais dans les deux.",
    )
    bf_common_area_id = fields.Many2one(
        "bf.property.common.area",
        string="Partie commune",
        ondelete="restrict",
        help="La partie commune où l'équipement est installé, lorsqu'il y en "
             "a une.",
    )
    bf_location_display = fields.Char(
        string="Emplacement",
        compute="_compute_bf_location_display",
        # 🔴 Le SEUL champ du module à porter un `groups=`, et l'en-tête dit
        # pourquoi les autres n'en ont pas : un `groups=` de champ fait échouer
        # les contrôles de cohérence et les duplications. Celui-ci n'entre dans
        # aucune contrainte et n'est lu par aucun calcul — il ne fait
        # qu'afficher. Sans lui, on a mesuré un refus
        # d'accès opaque pour les deux publics du module : le technicien de
        # maintenance sur `bf.property.building`, le concierge sur
        # `bf.property.unit`. Le `groups=` rend le refus lisible et l'arrête à
        # la porte : `read(["bf_location_display"])` répond maintenant « pas
        # assez de droits pour accéder aux champs "bf_location_display" », au
        # lieu d'une erreur venue du fond d'un modèle voisin.
        #
        # ⚠️ **Et sa portée s'arrête là.** Odoo n'applique le `groups=` d'un
        # champ qu'à l'API de lecture — `read`, `web_read`, `search_read`,
        # l'export. Un accès par attribut dans du code serveur
        # (`equipment.bf_location_display`) calcule quand même, et lèvera
        # encore depuis le modèle voisin. C'est le client web et le RPC qui
        # sont couverts, c'est-à-dire tout ce qui vient du dehors ; un appelant
        # interne qui veut ce texte pour quelqu'un qui n'y a pas droit doit
        # savoir ce qu'il fait.
        #
        # ⚠️ La fraction est ce qui rend ce champ réservé. L'immeuble seul, le
        # concierge l'a — il est sur ses écrans — mais l'emplacement
        # composé nomme la fraction, et la barrière entre équipes et fractions
        # la lui ferme.
        groups="bf_property_core.group_bf_property_user",
        help="Ce que le rattachement au bâti donne à lire, sans remplacer le "
             "texte libre d'origine : les deux coexistent, parce qu'une "
             "précision comme « sous-sol, local 3 » n'a pas de champ.",
    )

    @api.depends(
        "bf_building_id", "bf_unit_id", "bf_common_area_id", "location"
    )
    def _compute_bf_location_display(self):
        for equipment in self:
            parts = [
                equipment.bf_building_id.display_name,
                equipment.bf_unit_id.display_name
                or equipment.bf_common_area_id.display_name,
                equipment.location,
            ]
            equipment.bf_location_display = " / ".join(part for part in parts if part)

    # Mesuré : Odoo CUMULE les `depends` de la chaîne
    # d'héritage, il ne les remplace pas — `field_depends` rend ici
    # ('bf_building_id', 'company_id') même en retirant `company_id` d'ici.
    # On le répète quand même : la surcharge se lit seule, et la lecture ne
    # doit pas dépendre d'un comportement du cadre qu'on ne voit pas.
    @api.depends("company_id", "bf_building_id")
    def _compute_maintenance_team_id(self):
        super()._compute_maintenance_team_id()
        for equipment in self:
            # sudo : un technicien sans les droits de la suite écrit des
            # équipements, et le calcul ne doit pas se transformer en refus
            # d'accès. Voir l'en-tête du fichier.
            team = equipment.bf_building_id.sudo().bf_maintenance_team_id
            if team and not equipment.maintenance_team_id:
                equipment.maintenance_team_id = team

    @api.onchange("bf_unit_id", "bf_common_area_id")
    def _onchange_bf_place(self):
        """L'immeuble se déduit de la fraction ou de la partie commune choisie.

        L'écran le remplit pour que personne n'ait à répéter une information
        déductible ; `create` et `write` font le même travail pour tout ce qui
        n'entre pas par un écran.
        """
        for equipment in self:
            anchor = (
                equipment.bf_unit_id.building_id
                or equipment.bf_common_area_id.building_id
            )
            if anchor:
                equipment.bf_building_id = anchor

    @api.model
    def _bf_fill_building(self, vals):
        """Déduire l'immeuble de la fraction ou de la partie commune.

        ⚠️ Un import par RPC n'appelle aucun `onchange`. Sans cette déduction,
        un chargement qui nomme la fraction et tait l'immeuble échouerait au
        contrôle de cohérence pour n'avoir pas répété ce que la fraction dit
        déjà.
        """
        if vals.get("bf_building_id"):
            return vals
        unit_id = vals.get("bf_unit_id")
        area_id = vals.get("bf_common_area_id")
        anchor = self.env["bf.property.building"]
        if unit_id:
            anchor = self.env["bf.property.unit"].sudo().browse(unit_id).building_id
        elif area_id:
            anchor = (
                self.env["bf.property.common.area"].sudo().browse(area_id).building_id
            )
        if anchor:
            vals = dict(vals, bf_building_id=anchor.id)
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        return super().create([self._bf_fill_building(vals) for vals in vals_list])

    def write(self, vals):
        if "bf_unit_id" in vals or "bf_common_area_id" in vals:
            vals = self._bf_fill_building(vals)
        return super().write(vals)

    @api.constrains("bf_building_id", "bf_unit_id", "bf_common_area_id")
    def _check_bf_place(self):
        # sudo : voir l'en-tête du fichier. On valide des données, on n'ouvre
        # aucune lecture à l'utilisateur.
        for equipment in self.sudo():
            if equipment.bf_unit_id and equipment.bf_common_area_id:
                raise ValidationError(
                    _(
                        "« %(equipment)s » est rattaché à la fois à la fraction "
                        "%(unit)s et à la partie commune %(area)s. Un équipement "
                        "est dans l'une ou dans l'autre : c'est ce partage qui "
                        "dit qui en répond.",
                        equipment=equipment.display_name,
                        unit=equipment.bf_unit_id.display_name,
                        area=equipment.bf_common_area_id.display_name,
                    )
                )
            anchor = (
                equipment.bf_unit_id.building_id
                or equipment.bf_common_area_id.building_id
            )
            if anchor and not equipment.bf_building_id:
                raise ValidationError(
                    _(
                        "« %(equipment)s » n'est rattaché à aucun immeuble, "
                        "alors que l'emplacement choisi appartient à "
                        "%(anchor)s.",
                        equipment=equipment.display_name,
                        anchor=anchor.display_name,
                    )
                )
            if anchor and anchor != equipment.bf_building_id:
                raise ValidationError(
                    _(
                        "« %(equipment)s » est rattaché à l'immeuble "
                        "%(building)s, mais l'emplacement choisi appartient à "
                        "%(anchor)s.",
                        equipment=equipment.display_name,
                        building=equipment.bf_building_id.display_name,
                        anchor=anchor.display_name,
                    )
                )
            company = equipment.bf_building_id.company_id
            if company and equipment.company_id and company != equipment.company_id:
                raise ValidationError(
                    _(
                        "« %(equipment)s » appartient à %(company)s et "
                        "l'immeuble %(building)s à %(other)s.",
                        equipment=equipment.display_name,
                        company=equipment.company_id.display_name,
                        building=equipment.bf_building_id.display_name,
                        other=company.display_name,
                    )
                )

    # ── Le préventif cédulé ──

    bf_plan_ids = fields.One2many(
        "bf.property.maintenance.plan",
        "equipment_id",
        string="Cédules d'entretien",
        help="Les entretiens préventifs qui se répètent sur ce bien. Un même "
             "bien en porte souvent plusieurs : la chaudière s'inspecte deux "
             "fois l'an et son filtre se change tous les trois mois.",
    )
    bf_plan_count = fields.Integer(
        string="Nombre de cédules", compute="_compute_bf_plan_count"
    )
    bf_next_preventive_date = fields.Date(
        string="Prochain entretien prévu",
        compute="_compute_bf_next_preventive_date",
        help="La plus proche des échéances de ses cédules. Vide quand le bien "
             "n'a aucun entretien préventif cédulé, ce qui est une "
             "information, pas un vide.",
    )

    @api.depends("bf_plan_ids")
    def _compute_bf_plan_count(self):
        for equipment in self:
            equipment.bf_plan_count = len(equipment.bf_plan_ids)

    @api.depends("bf_plan_ids.next_date", "bf_plan_ids.active")
    def _compute_bf_next_preventive_date(self):
        for equipment in self:
            dates = equipment.bf_plan_ids.filtered("next_date").mapped("next_date")
            equipment.bf_next_preventive_date = min(dates, default=False)

    def action_view_bf_plans(self):
        # 🔴 Même garde que `action_view_requests` du cédule, et pour la même
        # raison : appelable par RPC, `ensure_one()` ne lit rien, et le
        # dictionnaire d'action se compose sans toucher la base. Une méthode
        # qui rend un écran sur un enregistrement que l'appelant ne peut pas
        # lire n'a aucune raison de répondre.
        self.ensure_one()
        self.check_access("read")
        return {
            "type": "ir.actions.act_window",
            "name": _("Cédules d'entretien"),
            "res_model": "bf.property.maintenance.plan",
            "view_mode": "list,form",
            "domain": [("equipment_id", "=", self.id)],
            "context": {"default_equipment_id": self.id},
        }
