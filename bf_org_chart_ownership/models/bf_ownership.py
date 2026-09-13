# -*- coding: utf-8 -*-
"""La détention se range sur l'ARÊTE, pas dans la boîte.

C'est toute la différence avec un organigramme de personnes, et la raison pour
laquelle la vue hiérarchique d'Odoo ne peut pas la dessiner : elle ne sait
suivre qu'un seul lien replié sur le même modèle, et n'a nulle part où lire un
pourcentage. Une société a plusieurs détenteurs, chacun pour une part, parfois
par catégorie d'actions, et à une date qui compte.

⚠️ Le total ne se contrôle pas en dur. Une structure connue à moitié est le cas
NORMAL chez un prospect : refuser d'enregistrer 60 % tant que les 40 % restants
sont inconnus reviendrait à n'enregistrer rien du tout.
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class BfOwnership(models.Model):
    _name = "bf.ownership"
    _description = "Lien de détention"
    _order = "owned_id, percent desc, id"
    _rec_name = "display_name"

    owner_id = fields.Many2one(
        "res.partner", string="Détenteur", required=True, index=True,
        ondelete="cascade",
        help="La personne ou la société qui détient.")
    owned_id = fields.Many2one(
        "res.partner", string="Société détenue", required=True, index=True,
        ondelete="cascade")
    percent = fields.Float(
        string="Pourcentage détenu", digits=(6, 3), default=100.0,
        help="Part du capital détenue, en pourcentage. 100 = détention entière.")
    share_class = fields.Char(
        string="Catégorie d'actions",
        help="Catégorie A, B, privilégiées… Laisser vide si la structure ne "
             "distingue pas les catégories.")
    voting = fields.Boolean(
        string="Droit de vote", default=True,
        help="Décoché pour une participation sans droit de vote : le dessin le "
             "montre en pointillé.")
    date_effet = fields.Date(string="En vigueur depuis")
    date_fin = fields.Date(
        string="Jusqu'au",
        help="Renseigné, le lien est historique : il ne se dessine plus.")
    origine = fields.Selection(
        [("registre", "Registre public"),
         ("client", "Déclaration du client"),
         ("convention", "Convention entre actionnaires"),
         ("estimation", "Estimation à valider")],
        string="Source", default="client", required=True,
        help="D'où vient l'information. Une estimation se dessine en ambre.")
    note = fields.Char(string="Précision")
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company", string="Société", default=lambda s: s.env.company)

    _sql_constraints = [
        ("percent_borne", "CHECK (percent >= 0 AND percent <= 100)",
         "Un pourcentage de détention se tient entre 0 et 100."),
        ("lien_unique",
         "UNIQUE (owner_id, owned_id, share_class, date_effet)",
         "Ce lien de détention est déjà inscrit pour cette catégorie et cette date."),
    ]

    @api.depends("owner_id", "owned_id", "percent")
    def _compute_display_name(self):
        for lien in self:
            lien.display_name = "%s → %s (%s)" % (
                lien.owner_id.display_name or "?",
                lien.owned_id.display_name or "?",
                lien._pourcentage_lisible())

    def _pourcentage_lisible(self):
        self.ensure_one()
        valeur = self.percent or 0.0
        texte = ("%.3f" % valeur).rstrip("0").rstrip(".")
        return "%s %%" % (texte or "0")

    @api.constrains("owner_id", "owned_id")
    def _check_pas_soi_meme(self):
        for lien in self:
            if lien.owner_id == lien.owned_id:
                raise ValidationError(_("Une société ne se détient pas elle-même."))

    @api.constrains("owner_id", "owned_id", "active")
    def _check_pas_de_boucle(self):
        """Une détention circulaire est une saisie fausse, pas une structure.

        La détention croisée existe en droit, mais en cercle fermé elle rend le
        capital introuvable : c'est presque toujours une erreur de sens de
        lecture (« détenteur » et « détenue » intervertis). On la refuse ici
        plutôt que de la découvrir à l'écran.
        """
        for lien in self:
            if not lien.active:
                continue
            vus, a_voir = set(), [lien.owned_id.id]
            while a_voir:
                courant = a_voir.pop()
                if courant in vus:
                    continue
                vus.add(courant)
                if courant == lien.owner_id.id:
                    raise ValidationError(_(
                        "Boucle de détention : %(a)s finirait par se détenir "
                        "lui-même en passant par %(b)s.",
                        a=lien.owner_id.display_name,
                        b=lien.owned_id.display_name))
                a_voir += self.search([
                    ("owner_id", "=", courant), ("active", "=", True),
                ]).mapped("owned_id").ids

    def _est_en_vigueur(self, a_la_date=None):
        self.ensure_one()
        jour = a_la_date or fields.Date.context_today(self)
        if self.date_fin and self.date_fin < jour:
            return False
        return True
