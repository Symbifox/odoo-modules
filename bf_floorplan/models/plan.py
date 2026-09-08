# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

# Au-delà, c'est un terrain, pas un étage ; et un viewBox de cette taille
# ferait souffrir le navigateur.
COTE_MAX = 100000.0


class BfFloorplan(models.Model):
    """Un étage : ses dimensions réelles, son fond, et ce qui vit dessus."""

    _name = "bf.floorplan"
    _description = "Plan d'étage"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "batiment, sequence, name"

    name = fields.Char(string="Plan", required=True, tracking=True)
    batiment = fields.Char(string="Bâtiment", tracking=True,
                           help="Ex. : « Siège social », « Entrepôt Nord ».")
    etage = fields.Char(string="Étage", tracking=True,
                        help="Ex. : « RDC », « 2 », « Sous-sol ».")
    sequence = fields.Integer(string="Ordre", default=10)
    partner_id = fields.Many2one(
        "res.partner", string="Site", tracking=True,
        help="L'organisation ou l'adresse du bâtiment.")
    company_id = fields.Many2one(
        "res.company", string="Société",
        default=lambda self: self.env.company)
    active = fields.Boolean(default=True)

    # Le fond est une image, jamais un SVG : un SVG tiers servi tel quel
    # emporte son script. fields.Image refuse tout ce qui n'est pas une
    # image matricielle, et plafonne la résolution.
    # copy=True : un étage sert de gabarit au suivant, fond compris (un Binary
    # ne se copie pas par défaut)
    fond = fields.Image(string="Fond de plan", attachment=True, copy=True,
                        help="PNG ou JPEG. Il est étiré aux dimensions du plan : "
                             "donnez au plan la largeur et la profondeur réelles "
                             "de ce que l'image montre.")
    fond_128 = fields.Image(string="Vignette", related="fond", max_width=256,
                            max_height=256, store=True)
    largeur = fields.Float(string="Largeur (cm)", default=2000.0, required=True)
    profondeur = fields.Float(string="Profondeur (cm)", default=1500.0, required=True)
    pas = fields.Float(string="Pas de la grille (cm)", default=25.0, required=True,
                       help="Ce sur quoi les formes se calent quand on les glisse.")
    verrouille = fields.Boolean(string="Plan figé", default=False, tracking=True,
                                help="Un plan figé se consulte et s'imprime, "
                                     "mais ne se modifie plus.")
    notes = fields.Html(string="Notes")

    zone_ids = fields.One2many("bf.floorplan.zone", "plan_id", string="Zones")
    element_ids = fields.One2many("bf.floorplan.element", "plan_id", string="Éléments")
    lien_ids = fields.One2many("bf.floorplan.lien", "plan_id", string="Liens")
    zone_count = fields.Integer(compute="_compute_counts", string="Zones")
    element_count = fields.Integer(compute="_compute_counts", string="Éléments")
    lien_count = fields.Integer(compute="_compute_counts", string="Liens")
    places = fields.Integer(compute="_compute_occupation", string="Places",
                            help="Somme des capacités des zones.")
    occupes = fields.Integer(compute="_compute_occupation", string="Postes occupés",
                             help="Postes de travail auxquels une personne est affectée.")

    @api.depends("zone_ids", "element_ids", "lien_ids")
    def _compute_counts(self):
        for plan in self:
            plan.zone_count = len(plan.zone_ids)
            plan.element_count = len(plan.element_ids)
            plan.lien_count = len(plan.lien_ids)

    @api.depends("zone_ids.capacite", "element_ids.genre", "element_ids.employee_id")
    def _compute_occupation(self):
        for plan in self:
            plan.places = sum(plan.zone_ids.mapped("capacite"))
            plan.occupes = len(plan.element_ids.filtered(
                lambda e: e.genre == "poste" and e.employee_id))

    @api.constrains("largeur", "profondeur", "pas")
    def _check_dimensions(self):
        for plan in self:
            if plan.largeur <= 0 or plan.profondeur <= 0:
                raise ValidationError(_("Un plan a une largeur et une profondeur "
                                        "positives, en centimètres."))
            if plan.largeur > COTE_MAX or plan.profondeur > COTE_MAX:
                raise ValidationError(_("Un plan ne dépasse pas %s cm de côté.")
                                      % int(COTE_MAX))
            if plan.pas <= 0:
                raise ValidationError(_("Le pas de la grille est positif."))

    @api.depends("name", "batiment", "etage")
    def _compute_display_name(self):
        for plan in self:
            morceaux = [plan.batiment, plan.etage and _("étage %s") % plan.etage]
            suffixe = ", ".join(m for m in morceaux if m)
            plan.display_name = f"{plan.name} ({suffixe})" if suffixe else plan.name

    # --- gel ----------------------------------------------------------------

    def _modifiable(self):
        """Le plan accepte-t-il d'être travaillé par la personne connectée ?

        Lu par le composant avant d'offrir une poignée : laisser glisser une
        forme pour rendre une erreur au relâchement serait une promesse
        qu'on ne tient pas.
        """
        self.ensure_one()
        return (self.active and not self.verrouille
                and self.env["bf.floorplan.element"].has_access("write"))

    def _exiger_modifiable(self):
        self.ensure_one()
        if self.verrouille:
            raise UserError(_("« %s » est figé : rouvrez-le pour le modifier.")
                            % self.display_name)
        if not self.active:
            raise UserError(_("« %s » est archivé.") % self.display_name)
        self.env["bf.floorplan.element"].check_access("write")

    def action_figer(self):
        for plan in self:
            plan.verrouille = True
            # _message_log : une note de journal, sans courriel, donc sans
            # exiger que la personne ait une adresse
            plan._message_log(body=_("Plan figé."))

    def action_rouvrir(self):
        for plan in self:
            plan.verrouille = False
            plan._message_log(body=_("Plan rouvert."))

    # --- copie --------------------------------------------------------------

    def copy(self, default=None):
        """Dupliquer un plan emporte ses zones, ses éléments et ses liens.

        Les liens pointent des éléments : copiés tels quels, ils viseraient
        ceux du plan d'origine. Zones et éléments sont donc copiés un par un,
        la correspondance tenue à la main, et les liens refaits dessus.
        """
        self.ensure_one()
        default = dict(default or {}, name=_("%s (copie)") % self.name,
                       zone_ids=False, element_ids=False, lien_ids=False,
                       verrouille=False)
        neuf = super().copy(default)
        for zone in self.zone_ids:
            zone.copy({"plan_id": neuf.id})
        corresp = {}
        # les éléments archivés aussi : un lien peut encore y pointer, et un
        # élément absent de la correspondance ferait échouer la copie entière
        for el in self.with_context(active_test=False).element_ids:
            corresp[el.id] = el.copy({"plan_id": neuf.id}).id
        for lien in self.lien_ids:
            if lien.src_id.id not in corresp or lien.dst_id.id not in corresp:
                continue
            self.env["bf.floorplan.lien"].create({
                "plan_id": neuf.id,
                "src_id": corresp[lien.src_id.id],
                "dst_id": corresp[lien.dst_id.id],
                "genre": lien.genre,
                "name": lien.name,
            })
        return neuf

    # --- ouvertures ---------------------------------------------------------

    def action_voir_elements(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Éléments de %s") % self.display_name,
            "res_model": "bf.floorplan.element",
            "view_mode": "list,form",
            "domain": [("plan_id", "=", self.id)],
            "context": {"default_plan_id": self.id},
        }

    def action_voir_zones(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Zones de %s") % self.display_name,
            "res_model": "bf.floorplan.zone",
            "view_mode": "list,form",
            "domain": [("plan_id", "=", self.id)],
            "context": {"default_plan_id": self.id},
        }
