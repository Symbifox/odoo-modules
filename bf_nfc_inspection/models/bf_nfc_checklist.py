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
    ("aucune", "Sans rythme"),
    ("jour", "Chaque jour"),
    ("semaine", "Chaque semaine"),
    ("mois", "Chaque mois"),
    ("an", "Chaque année"),
]


class BfNfcChecklist(models.Model):
    _name = "bf.nfc.checklist"
    _description = "Grille de relevé"
    _inherit = ["mail.thread"]
    _order = "sequence, name"

    name = fields.Char(string="Grille", required=True, tracking=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True, tracking=True)
    company_id = fields.Many2one("res.company", string="Société",
                                 help="Vide : partagée par toutes les sociétés.")
    description = fields.Text(translate=True)
    reference = fields.Char(
        string="Référence", translate=True,
        help="La règle ou la norme que ce relevé sert à prouver, telle qu'on la citerait "
             "à une inspection.")
    periode = fields.Selection(
        PERIODES, string="Rythme", default="aucune", required=True, tracking=True,
        help="Une pastille qui porte cette grille et qui n'a pas été relevée dans la "
             "période crée une activité pour la personne responsable.")
    responsible_id = fields.Many2one(
        "res.users", string="Responsable", tracking=True,
        help="Reçoit les anomalies et les relevés manqués.")
    item_ids = fields.One2many("bf.nfc.checklist.item", "checklist_id", string="Éléments", copy=True)
    item_count = fields.Integer(compute="_compute_item_count")
    a_valider = fields.Boolean(
        string="À valider",
        help="Grille livrée comme point de départ : ses éléments et ses fréquences doivent "
             "être confirmés contre la norme et l'équipement réels avant de s'en servir.")

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
            champs.append({"cle": "_nom", "libelle": _("Votre nom"), "type": "texte",
                           "requis": True, "unite": None, "min": None, "max": None,
                           "options": None, "aide": _("Le registre dit qui a vérifié.")})
        for element in self.item_ids:
            champs.append(element._champ())
        return champs


class BfNfcChecklistItem(models.Model):
    _name = "bf.nfc.checklist.item"
    _description = "Élément d'une grille de relevé"
    _order = "checklist_id, sequence, id"

    checklist_id = fields.Many2one("bf.nfc.checklist", required=True, ondelete="cascade", index=True)
    sequence = fields.Integer(default=10)
    name = fields.Char(string="Élément", required=True, translate=True)
    kind = fields.Selection(
        [("conforme", "Conforme / non conforme"), ("nombre", "Valeur mesurée"),
         ("choix", "Choix"), ("texte", "Texte")],
        string="Réponse", default="conforme", required=True)
    required = fields.Boolean(string="Requis", default=True)
    unit = fields.Char(string="Unité", translate=True)
    min_value = fields.Float(string="Minimum")
    max_value = fields.Float(string="Maximum")
    has_range = fields.Boolean(string="Plage", help="Une valeur hors de la plage est une anomalie.")
    options = fields.Char(string="Options", translate=True,
                          help="Les choix proposés, séparés par des points-virgules.")
    anomaly_options = fields.Char(string="Options en anomalie", translate=True,
                                  help="Parmi les options, celles qui signalent une anomalie.")
    help = fields.Char(string="Aide", translate=True)

    @api.constrains("kind", "options")
    def _check_options(self):
        for element in self:
            if element.kind == "choix" and len(element._liste(element.options)) < 2:
                raise ValidationError(_("« %s » : un choix propose au moins deux options.", element.name))

    @api.constrains("has_range", "min_value", "max_value")
    def _check_plage(self):
        for element in self:
            if element.has_range and element.min_value > element.max_value:
                raise ValidationError(_("« %s » : le minimum dépasse le maximum.", element.name))

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
