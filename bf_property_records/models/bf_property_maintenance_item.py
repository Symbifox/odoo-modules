"""Un bien au carnet d'entretien.

RLRQ, c. CCQ, r. 8.01, art. 2 et 3. L'art. 2 veut l'inventaire et la description
des parties communes et des matériaux, appareils et équipements qui les
composent, **et** de ceux installés dans les parties privatives dont le syndicat
est responsable de l'entretien. L'art. 3 veut, dans une section consacrée
exclusivement à cette fin, l'estimation de l'état et de la durée de vie utile
restante, puis la description des réparations majeures et des remplacements à
effectuer durant minimalement les 25 prochaines années, avec une année de
réalisation estimée pour chacun.

⚠️ Le module ne calcule aucune de ces estimations. Elles sont le travail de la
personne qui signe le carnet, et le plafond de la tâche est explicite : le
module produit les documents et les traces, il ne se prononce jamais sur la
suffisance de quoi que ce soit.

⚠️ Un bien d'une partie privative n'entre au carnet que si le SYNDICAT en
répond. La case existe pour cela, et elle est nécessaire : sans elle, un carnet
finirait par décrire les rénovations des copropriétaires.

🔴 **Un champ écrit d'après le règlement et laissé hors écran ne vaut rien**
(défaut trouvé par une passe statique). Sept champs de ce modèle
étaient dans ce cas : la description, les réparations courantes, les contrats,
les inspections, le manuel du fabricant, et les travaux réalisés avec leur coût.
Le module tenait donc un carnet auquel un syndicat ne pouvait porter ni quatre
des sept paragraphes de l'art. 2 al. 2, ni ce que l'art. 3 al. 2 fait noter. Ils
existaient, ils étaient corrects, et aucune vue ne les montrait : la fonction
paraissait faite.

Tout champ de ce modèle dont l'aide cite **`r. 8.01`** doit donc se trouver sur
l'écran du carnet, et un test l'éprouve en tirant sa liste des aides elles-mêmes
plutôt que d'une énumération tapée à la main. Écrire un champ neuf d'après le
règlement sans lui donner d'écran fait échouer ce test.
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

CONDITION_LEVELS = [
    ("good", "Bon"),
    ("fair", "Passable"),
    ("poor", "Mauvais"),
    ("end_of_life", "Fin de vie utile"),
]


class BfPropertyMaintenanceItem(models.Model):
    _name = "bf.property.maintenance.item"
    _description = "Bien au carnet d'entretien"
    _order = "log_id, major_work_year, name"

    name = fields.Char(string="Bien", required=True)
    log_id = fields.Many2one(
        "bf.property.maintenance.log",
        string="Carnet",
        required=True,
        ondelete="cascade",
        index=True,
    )
    organisation_id = fields.Many2one(
        related="log_id.organisation_id", store=True, string="Syndicat"
    )
    company_id = fields.Many2one(
        related="log_id.company_id", store=True, string="Société"
    )
    building_id = fields.Many2one(
        "bf.property.building",
        string="Immeuble",
        index=True,
        domain="[('organisation_id', '=', organisation_id)]",
        help="Un syndicat peut regrouper plusieurs immeubles, et le carnet ne "
             "disait pas dans lequel un bien se trouve : la partie commune le "
             "disait pour lui, et un bien de partie privative ne le disait "
             "nulle part.",
    )
    common_area_id = fields.Many2one(
        "bf.property.common.area",
        string="Partie commune",
        domain="[('organisation_id', '=', organisation_id)]",
    )
    unit_id = fields.Many2one(
        "bf.property.unit",
        string="Fraction",
        domain="[('building_id', '=', building_id)]",
        help="r. 8.01, art. 2 al. 1 : le carnet couvre les matériaux, "
             "appareils et équipements installés dans les parties privatives "
             "dont le syndicat est responsable de l'entretien. Le règlement "
             "n'exige pas de nommer la fraction ; y envoyer quelqu'un, oui.",
    )
    in_private_portion = fields.Boolean(
        string="Situé dans une partie privative",
        help="r. 8.01, art. 2 al. 1 : le carnet couvre aussi les matériaux, appareils "
             "et équipements installés dans les parties privatives dont le "
             "SYNDICAT est responsable de l'entretien.",
    )
    syndicat_maintains = fields.Boolean(
        string="Entretien à la charge du syndicat",
        default=True,
        help="Sans cette responsabilité, un bien de partie privative n'a rien "
             "à faire au carnet.",
    )
    description = fields.Text(
        string="Description",
        help="r. 8.01, art. 2 al. 1 : le carnet porte l'inventaire ET la "
             "description des parties communes, des matériaux, des appareils "
             "et des équipements qui les composent.",
    )

    # ── Art. 2 : ce qui se constate ──
    install_date = fields.Date(
        string="Installé le",
        help="r. 8.01, art. 2 al. 2, par. 1° : « la date d'installation, si "
             "connue ».",
    )
    maintenance_frequency = fields.Char(
        string="Fréquence d'entretien",
        help="r. 8.01, art. 2 al. 2, par. 2° : les travaux d'entretien requis "
             "et la fréquence à laquelle ils doivent être effectués.",
    )
    last_maintenance_date = fields.Date(
        string="Dernier entretien",
        help="r. 8.01, art. 2 al. 2, par. 2° : la date de réalisation des "
             "travaux d'entretien requis.",
    )
    last_repair_date = fields.Date(
        string="Dernière réparation courante",
        help="r. 8.01, art. 2 al. 2, par. 3° : les réparations courantes et "
             "leur date.",
    )
    contract_reference = fields.Char(
        string="Contrats",
        help="r. 8.01, art. 2 al. 2, par. 4° et 5° : contrats de réalisation "
             "des travaux et contrats de garantie en vigueur.",
    )
    inspection_reference = fields.Char(
        string="Inspections et expertises",
        help="r. 8.01, art. 2 al. 2, par. 6° : rapports d'inspection ou "
             "d'expertise.",
    )
    manual_reference = fields.Char(
        string="Manuel du fabricant",
        help="r. 8.01, art. 2 al. 2, par. 7° : manuels d'entretien du "
             "fabricant.",
    )

    # ── Art. 3 : ce qui s'estime, et que le module ne calcule pas ──
    condition = fields.Selection(
        CONDITION_LEVELS,
        string="État estimé",
        help="r. 8.01, art. 3 : estimation portée par la personne qui signe "
             "le carnet. Le module ne l'évalue pas.",
    )
    remaining_life_years = fields.Integer(
        string="Durée de vie utile restante (ans)",
        help="r. 8.01, art. 3 : estimation de l'auteur du carnet.",
    )
    major_work = fields.Char(
        string="Réparation majeure ou remplacement prévu",
        help="r. 8.01, art. 3 : description des réparations majeures et des "
             "remplacements à effectuer.",
    )
    major_work_year = fields.Integer(
        string="Année de réalisation estimée",
        help="r. 8.01, art. 3 : « Une année de réalisation estimée doit être "
             "indiquée pour chaque réparation majeure et remplacement à "
             "effectuer. »",
    )
    major_work_cost = fields.Monetary(
        string="Coût estimé",
        currency_field="currency_id",
        help="r. 8.01, art. 3 : coût estimé de la réparation majeure ou du "
             "remplacement, porté par l'auteur du carnet.",
    )
    currency_id = fields.Many2one(
        related="company_id.currency_id", string="Devise"
    )

    # ── Art. 3 al. 2 : ce qui a été fait ; art. 4 : ce qui ne l'a pas été ──
    done_date = fields.Date(
        string="Travaux réalisés le",
        help="r. 8.01, art. 3 al. 2 : « Les travaux effectués, leur date et "
             "leur coût y sont notés. »",
    )
    done_cost = fields.Monetary(
        string="Coût réel",
        currency_field="currency_id",
        help="r. 8.01, art. 3 al. 2 : le coût des travaux effectués, tel "
             "qu'il a été, et non le coût qui avait été estimé.",
    )
    not_done_reason = fields.Char(
        string="Non effectué, parce que",
        help="r. 8.01, art. 4 : lors de la mise à jour annuelle, si des "
             "travaux requis ou prévus n'ont pas été effectués, le carnet le "
             "mentionne ET indique pourquoi. Le module ne juge pas qu'un "
             "travail aurait dû l'être : il montre ceux dont l'année estimée "
             "est passée sans date de réalisation, et la raison vient du "
             "conseil.",
    )
    in_horizon = fields.Boolean(
        string="Dans l'horizon des 25 ans",
        compute="_compute_in_horizon",
        store=True,
        help="Art. 3 : la description porte sur minimalement les 25 "
             "prochaines années. Un travail prévu au-delà est une information "
             "de plus, pas une exigence du règlement.",
    )

    _sql_constraints = [
        (
            "remaining_life_positive",
            "CHECK(remaining_life_years >= 0)",
            "Une durée de vie utile restante ne peut pas être négative.",
        ),
    ]

    @api.depends("major_work_year", "log_id.established_date")
    def _compute_in_horizon(self):
        for item in self:
            established = item.log_id.established_date
            if not item.major_work_year or not established:
                item.in_horizon = False
                continue
            item.in_horizon = (
                established.year <= item.major_work_year
                <= item.log_id.planning_horizon_date.year
            )

    @api.constrains("building_id", "common_area_id", "unit_id", "log_id")
    def _check_place(self):
        """Le bien est quelque part, et il n'est pas à deux endroits.

        ⚠️ La déduction de `create` et `write` comble un vide ; elle ne corrige
        personne. Un immeuble donné explicitement qui contredit la partie
        commune ou la fraction lève l'erreur plutôt que d'être remplacé en
        silence par la bonne valeur.
        """
        for item in self:
            if item.unit_id and item.common_area_id:
                raise ValidationError(
                    _(
                        "« %(item)s » est porté à la fois à la fraction "
                        "%(unit)s et à la partie commune %(area)s. Un bien est "
                        "dans l'une ou dans l'autre : c'est ce partage qui dit "
                        "si le syndicat en répond de plein droit.",
                        item=item.name or "",
                        unit=item.unit_id.display_name,
                        area=item.common_area_id.display_name,
                    )
                )
            log_building = item.log_id.building_id
            if (
                log_building
                and item.building_id
                and log_building != item.building_id
            ):
                raise ValidationError(
                    _(
                        "Le carnet « %(log)s » porte sur l'immeuble "
                        "%(log_building)s, et « %(item)s » est porté à "
                        "%(building)s.",
                        log=item.log_id.display_name,
                        log_building=log_building.display_name,
                        item=item.name or "",
                        building=item.building_id.display_name,
                    )
                )
            anchor = item.unit_id.building_id or item.common_area_id.building_id
            if anchor and not item.building_id:
                raise ValidationError(
                    _(
                        "« %(item)s » n'est porté à aucun immeuble, alors que "
                        "l'emplacement choisi appartient à %(anchor)s.",
                        item=item.name or "",
                        anchor=anchor.display_name,
                    )
                )
            if anchor and anchor != item.building_id:
                raise ValidationError(
                    _(
                        "« %(item)s » est porté à l'immeuble %(building)s, "
                        "mais l'emplacement choisi appartient à %(anchor)s.",
                        item=item.name or "",
                        building=item.building_id.display_name,
                        anchor=anchor.display_name,
                    )
                )

    @api.constrains("unit_id", "in_private_portion")
    def _check_unit_is_a_private_portion(self):
        for item in self:
            if item.unit_id and not item.in_private_portion:
                raise ValidationError(
                    _(
                        "« %(item)s » nomme la fraction %(unit)s sans être "
                        "déclaré situé dans une partie privative. L'art. 2 du "
                        "règlement fait reposer le carnet sur cette "
                        "distinction : cochez la case, ou retirez la fraction.",
                        item=item.name or "",
                        unit=item.unit_id.display_name,
                    )
                )

    @api.model
    def _fill_building(self, vals):
        """Déduire l'immeuble de la partie commune ou de la fraction.

        ⚠️ Un import par RPC n'appelle aucun `onchange` : sans cette déduction,
        un chargement qui nomme la partie commune et tait l'immeuble échouerait
        au contrôle pour n'avoir pas répété ce que la partie commune dit déjà.
        """
        if vals.get("building_id"):
            return vals
        anchor = self.env["bf.property.building"]
        if vals.get("unit_id"):
            anchor = self.env["bf.property.unit"].browse(vals["unit_id"]).building_id
        elif vals.get("common_area_id"):
            anchor = (
                self.env["bf.property.common.area"]
                .browse(vals["common_area_id"])
                .building_id
            )
        elif vals.get("log_id"):
            # Un carnet peut nommer son immeuble : le règlement raisonne par
            # bâti. Quand il le fait, ses biens n'ont pas à le répéter.
            anchor = (
                self.env["bf.property.maintenance.log"]
                .browse(vals["log_id"])
                .building_id
            )
        if anchor:
            vals = dict(vals, building_id=anchor.id)
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        return super().create([self._fill_building(vals) for vals in vals_list])

    def write(self, vals):
        # ⚠️ `log_id` n'est PAS dans ce jeu, et c'est délibéré : déplacer un
        # bien d'un carnet à l'autre ne doit pas le relocaliser en silence
        # dans l'immeuble du carnet d'arrivée. Si les deux se contredisent, le
        # contrôle le dit. Choisir une partie commune ou une fraction, en
        # revanche, EST un déplacement, et l'immeuble suit.
        if {"common_area_id", "unit_id"} & set(vals):
            vals = self._fill_building(vals)
        return super().write(vals)

    @api.onchange("common_area_id", "unit_id")
    def _onchange_place(self):
        for item in self:
            anchor = item.unit_id.building_id or item.common_area_id.building_id
            if anchor:
                item.building_id = anchor

    @api.constrains("in_private_portion", "syndicat_maintains")
    def _check_private_portion(self):
        for item in self:
            if item.in_private_portion and not item.syndicat_maintains:
                raise ValidationError(
                    _(
                        "Art. 2 du règlement : un bien situé dans une partie "
                        "privative n'entre au carnet que si le syndicat est "
                        "responsable de son entretien. Retirez-le, ou portez "
                        "cette responsabilité."
                    )
                )

    @api.depends("name", "log_id")
    def _compute_display_name(self):
        for item in self:
            item.display_name = item.name or ""
