"""La grille : ce qu'on vérifie à chaque relevé, et ce qui compte comme une anomalie.

⚠️ Une grille se modifie avec le temps (on ajoute un élément, on en renomme un).
Un relevé déjà fait ne doit pas changer pour autant : chaque ligne de relevé
recopie le libellé, l'unité et la plage de l'élément au moment du tapotement. Le
registre de mars dit ce qu'on vérifiait en mars.
"""
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

PERIODES = [
    ("aucune", "No frequency"),
    ("jour", "Every day"),
    ("semaine", "Every week"),
    ("mois", "Every month"),
    ("an", "Every year"),
]


class BfNfcChecklist(models.Model):
    _name = "bf.nfc.checklist"
    _description = "Inspection checklist"
    _inherit = ["mail.thread"]
    _order = "sequence, name"

    name = fields.Char(string="Checklist", required=True, tracking=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True, tracking=True)
    company_id = fields.Many2one("res.company", string="Company",
                                 help="Empty: shared by all companies.")
    description = fields.Text(translate=True)
    reference = fields.Char(
        string="Reference", translate=True,
        help="The rule or standard this reading serves to prove, as you "
             "would cite it during an inspection.")
    periode = fields.Selection(
        PERIODES, string="Frequency", default="aucune", required=True, tracking=True,
        help="A tag that carries this checklist and has not been read "
             "within the period creates an activity for the person in "
             "charge.")
    responsible_id = fields.Many2one(
        "res.users", string="Person in charge", tracking=True,
        help="Receives issues and missed readings.")
    item_ids = fields.One2many("bf.nfc.checklist.item", "checklist_id", string="Items", copy=True)
    item_count = fields.Integer(compute="_compute_item_count")
    a_valider = fields.Boolean(
        string="To validate",
        help="Checklist delivered as a starting point: its items and "
             "frequencies must be confirmed against the actual standard "
             "and equipment before it is used.")

    @api.depends("item_ids")
    def _compute_item_count(self):
        for grille in self:
            grille.item_count = len(grille.item_ids)

    def _debut_de_periode(self, maintenant):
        """Le début de la période en cours, en UTC naïf, ou None sans rythme."""
        self.ensure_one()
        return {
            "jour": maintenant - timedelta(days=1),
            "semaine": maintenant - timedelta(days=7),
            "mois": maintenant - relativedelta(months=1),
            "an": maintenant - relativedelta(years=1),
        }.get(self.periode)

    def _formulaire(self, porte=None):
        """La grille sous la forme du protocole ``formulaire`` du socle.

        🔴 Par la porte signée, la personne qui tape n'a pas de compte : le relevé
        serait signé du compte générique de la pastille. Un registre exige le nom de
        qui a vérifié, donc la grille le demande en premier.
        """
        self.ensure_one()
        champs = []
        if porte == "signed":
            champs.append({"cle": "_nom", "libelle": _("Your name"), "type": "texte",
                           "requis": True, "unite": None, "min": None, "max": None,
                           "options": None, "aide": _("The register shows "
                                                      "who checked.")})
        for element in self.item_ids:
            champs.append(element._champ())
        return champs


class BfNfcChecklistItem(models.Model):
    _name = "bf.nfc.checklist.item"
    _description = "Inspection checklist item"
    _order = "checklist_id, sequence, id"

    checklist_id = fields.Many2one("bf.nfc.checklist", required=True, ondelete="cascade", index=True)
    sequence = fields.Integer(default=10)
    name = fields.Char(string="Item", required=True, translate=True)
    kind = fields.Selection(
        [("conforme", "Compliant / non-compliant"), ("nombre", "Measured value"),
         ("choix", "Choice"), ("texte", "Text")],
        string="Answer", default="conforme", required=True)
    required = fields.Boolean(string="Required", default=True)
    unit = fields.Char(string="Unit", translate=True)
    min_value = fields.Float(string="Minimum")
    max_value = fields.Float(string="Maximum")
    has_range = fields.Boolean(string="Range", help="A value outside the "
                                                    "range is an issue.")
    options = fields.Char(string="Options", translate=True,
                          help="The choices offered, separated by semicolons.")
    anomaly_options = fields.Char(string="Issue options", translate=True,
                                  help="Among the options, those that "
                                       "indicate an issue.")
    help = fields.Char(string="Help", translate=True)

    @api.constrains("kind", "options")
    def _check_options(self):
        for element in self:
            if element.kind == "choix" and len(element._liste(element.options)) < 2:
                raise ValidationError(_("“%s”: a choice offers at least "
                                        "two options.", element.name))

    @api.constrains("has_range", "min_value", "max_value")
    def _check_plage(self):
        for element in self:
            if element.has_range and element.min_value > element.max_value:
                raise ValidationError(_("“%s”: the minimum exceeds the "
                                        "maximum.", element.name))

    @staticmethod
    def _liste(texte):
        return [morceau.strip() for morceau in (texte or "").split(";") if morceau.strip()]

    def _cle(self):
        return "e%s" % self.id

    def _champ(self):
        self.ensure_one()
        return {
            "cle": self._cle(),
            "libelle": self.name,
            "type": self.kind,
            "requis": self.required,
            "unite": self.unit or None,
            "min": self.min_value if self.has_range else None,
            "max": self.max_value if self.has_range else None,
            "options": self._liste(self.options) if self.kind == "choix" else None,
            "aide": self.help or None,
        }

    def _en_anomalie(self, valeur):
        """Vrai quand la réponse signale un problème. Une réponse vide n'en signale pas."""
        self.ensure_one()
        if valeur is None:
            return False
        if self.kind == "conforme":
            return valeur is False
        if self.kind == "nombre" and self.has_range:
            return not (self.min_value <= valeur <= self.max_value)
        if self.kind == "choix":
            return valeur in self._liste(self.anomaly_options)
        return False
