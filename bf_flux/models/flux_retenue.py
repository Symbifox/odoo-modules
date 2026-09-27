# -*- coding: utf-8 -*-
"""Un élément retenu par une liste, avec le motif qui l'a retenu.

Le motif est écrit sur chaque retenue pour que les règles se règlent sur des
cas réels plutôt qu'à l'estime.
"""
from odoo import api, fields, models


class FluxRetenue(models.Model):
    _name = "bf.flux.retenue"
    _description = "Élément retenu par une liste"
    _order = "date_publication desc, id desc"
    _rec_name = "titre"

    liste_id = fields.Many2one(
        "bf.flux.liste", string="Liste", required=True, ondelete="cascade",
        index=True)
    element_id = fields.Many2one(
        "bf.flux.element", string="Élément", required=True, ondelete="cascade",
        index=True)
    titre = fields.Char(related="element_id.titre")
    lien = fields.Char(related="element_id.lien")
    emetteur = fields.Char(related="element_id.emetteur")
    date_publication = fields.Datetime(
        related="element_id.date_publication", store=True)
    motifs = fields.Char("Retenu pour", readonly=True)
    etat = fields.Selection(
        [("a_juger", "À juger"), ("retenu", "Retenu"), ("ecarte", "Écarté")],
        string="État", default="retenu", required=True, index=True,
        help="Les règles retiennent. Un module de jugement peut ensuite "
             "écarter ce qui a passé les règles, en disant pourquoi.")
    note = fields.Integer(
        "Pertinence", help="Note de 0 à 100, posée par un module de jugement.")
    raison = fields.Char("Raison du jugement")
    diffusee = fields.Boolean("Diffusée au canal", readonly=True, index=True)
    rattrapage = fields.Boolean(
        "Rattrapage", readonly=True,
        help="Retenue en appliquant les règles aux éléments déjà reçus : "
             "jamais diffusée ni reprise au résumé courriel.")

    _sql_constraints = [
        ("liste_element_unique", "UNIQUE(liste_id, element_id)",
         "Cet élément est déjà retenu par cette liste."),
    ]

    def _flux_juger(self):
        """Point d'accroche du jugement. Sans module de jugement, les règles
        font foi : ce qui les a passées est retenu."""
        return True

    def _flux_diffuser(self):
        """Pose chaque retenue au canal Discuss de sa liste."""
        auteur = self.env.ref("base.partner_root")
        for liste in self.liste_id:
            a_poser = self.filtered(
                lambda r: r.liste_id == liste and not r.diffusee
                and not r.rattrapage)
            if not a_poser:
                continue
            canal = liste._flux_canal().sudo()
            for ret in a_poser.sorted(lambda r: r.date_publication or r.create_date):
                canal.message_post(
                    body=liste._flux_carte_html(ret.element_id, ret.motifs),
                    author_id=auteur.id,
                    message_type="comment",
                    subtype_xmlid="mail.mt_comment",
                )
            a_poser.write({"diffusee": True})
        return True

    def action_ouvrir_lien(self):
        return self.element_id.action_ouvrir_lien()

    def action_poser_projet(self):
        self.ensure_one()
        action = self.element_id.action_poser_projet()
        action["context"]["default_project_id"] = self.liste_id.project_ids[:1].id
        return action
