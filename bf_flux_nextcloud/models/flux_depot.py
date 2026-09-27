# -*- coding: utf-8 -*-
"""Le registre des dépôts : un fichier déposé, avec son empreinte."""
from odoo import fields, models


class FluxDepot(models.Model):
    _name = "bf.flux.depot"
    _description = "Fichier de corpus déposé au Nextcloud"
    _order = "chemin"
    _rec_name = "chemin"

    liste_id = fields.Many2one(
        "bf.flux.liste", required=True, ondelete="cascade", index=True)
    chemin = fields.Char("Chemin", required=True)
    empreinte = fields.Char("Empreinte SHA-1", required=True)
    taille = fields.Integer("Taille (octets)")
    date = fields.Datetime("Déposé le", default=fields.Datetime.now)

    _sql_constraints = [
        ("liste_chemin_unique", "UNIQUE(liste_id, chemin)",
         "Un seul enregistrement par fichier et par liste."),
    ]
