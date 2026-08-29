# -*- coding: utf-8 -*-
"""La prose du livrable, portée par la carte.

Une cartographie AS-IS sans ses hypothèses, ses questions ouvertes et son
registre de validation n'est pas un livrable : elle décrit une séquence, pas
une performance, et rien ne dit ce qu'elle suppose. Cette prose existait
jusqu'ici dans un fichier Python posé à côté de la carte, hors dépôt et hors
version. Portée en base, elle suit la carte : elle se gèle avec sa version,
elle se relit au portail, et régénérer le document après une retouche du
tracé redevient un geste.

Un seul modèle, avec un genre, plutôt qu'un modèle par section : les
hypothèses, les questions et les constats ont exactement la même forme — un
intitulé et un corps — et seul leur regroupement à l'impression diffère.
"""
import base64

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .erreurs import refus_lisible

GENRES = [
    ("couverture", "Bloc de couverture"),
    ("hypothese", "Hypothèse"),
    ("question", "Question ouverte"),
    ("constat", "Constat"),
    ("annexe", "Page d'annexe"),
    ("validation", "Ligne du registre de validation"),
]

# Ce qui se regroupe sur une même page d'annexe, et dans quel ordre.
PAGES = (
    ("hypothese", "Hypothèses retenues pour dessiner la carte"),
    ("question", "Questions ouvertes"),
    ("constat", "Ce que la carte fait apparaître"),
)


class BfProcessSection(models.Model):
    _name = "bf.process.section"
    _inherit = ["bf.process.gel"]
    _description = "Section du livrable d'une cartographie"
    _order = "kind, sequence, id"

    process_id = fields.Many2one(
        "bf.process", string="Cartographie", required=True,
        ondelete="cascade", index=True)
    sequence = fields.Integer(string="Ordre", default=10)
    kind = fields.Selection(GENRES, string="Genre", required=True,
                            default="hypothese")
    name = fields.Char(
        string="Intitulé", required=True,
        help="Le titre du bloc, l'énoncé de l'hypothèse, ou le rôle attendu"
             " au registre de validation.")
    body = fields.Html(
        string="Corps", sanitize=True,
        help="Le texte de la section. Pour une ligne de registre, le nom de"
             " la personne attendue.")

    @api.constrains("kind", "body")
    def _check_corps(self):
        """Une section vide est pire qu'une section absente.

        Elle occupe une place au sommaire, se numérote, et le lecteur cherche
        ce qu'elle devait dire. Le registre de validation fait exception : sa
        raison d'être est justement d'arriver vide, pour être rempli.
        """
        for rec in self:
            if rec.kind != "validation" and not (rec.body or "").strip():
                raise ValidationError(_(
                    "« %s » n'a pas de corps. Une section vide se numérote et"
                    " se lit comme une omission : écrivez-la, ou retirez-la."
                ) % rec.name)


class BfProcessDocument(models.Model):
    _inherit = "bf.process"

    section_ids = fields.One2many(
        "bf.process.section", "process_id", string="Sections du livrable")
    section_count = fields.Integer(compute="_compute_section_count")
    sous_titre = fields.Char(
        string="Sous-titre du document",
        help="La ligne sous le titre, en couverture. Par exemple : « état"
             " actuel (AS-IS), de l'ouverture de l'exercice aux états"
             " financiers ».")
    pied_document = fields.Char(
        string="Pied de page du document",
        help="Ce que porte le bas de chaque page : d'où vient la carte, et"
             " ce qui n'a pas été fait. À défaut, la première ligne de la"
             " source sert.")
    date_document = fields.Date(
        string="Date du document", default=fields.Date.context_today)

    @api.depends("section_ids")
    def _compute_section_count(self):
        for rec in self:
            rec.section_count = len(rec.section_ids)

    def _pied(self):
        """La ligne de pied, avec son repli sur la source."""
        self.ensure_one()
        if (self.pied_document or "").strip():
            return self.pied_document.strip()
        lignes = (self.source or "").strip().splitlines()
        return lignes[0] if lignes else ""

    def _sections(self, genre):
        self.ensure_one()
        return self.section_ids.filtered(lambda s: s.kind == genre).sorted(
            key=lambda s: (s.sequence, s.id))

    def action_ouvrir_sections(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Sections du livrable"),
            "res_model": "bf.process.section",
            "view_mode": "list,form",
            "domain": [("process_id", "=", self.id)],
            "context": {"default_process_id": self.id},
        }

    # ------------------------------------------------------------- livrable --
    def _document_meta(self):
        """Ce que la mise en page a besoin de savoir, et rien de plus."""
        self.ensure_one()
        client = self.partner_id.display_name or self.pool_name or ""
        date = fields.Date.to_string(self.date_document) if self.date_document else ""
        # « Brouillon » sur la couverture d'un livrable client se lit comme un
        # défaut de la carte, alors qu'il ne dit que l'état du modèle. Le
        # document BSI disait « projet, à valider » : la même chose, mais
        # adressée au lecteur.
        etat = {"brouillon": "projet, à valider", "valide": "validée",
                "obsolete": "version obsolète"}.get(self.state, "")
        return {
            "titre": self.name,
            "sous_titre": self.sous_titre or client,
            "ligne_source": " · ".join(x for x in (
                client, "v%s" % self.version, etat, date) if x),
            "droite": " · ".join(x for x in (
                client, "Cartographie des processus (AS-IS)",
                "v%s" % self.version) if x),
            "meta": date,
            "pied": self._pied(),
        }

    def _document_sections(self):
        """La prose, groupée par genre, dans la forme attendue du générateur."""
        self.ensure_one()
        groupes = {}
        for genre, _libelle in GENRES:
            groupes[genre] = [(s.name, s.body or "")
                              for s in self._sections(genre)]
        return groupes

    @refus_lisible
    def exporter_document_pdf(self):
        """Le livrable en base64 — la forme qui traverse XML-RPC."""
        self.ensure_one()
        return base64.b64encode(self._document_octets()).decode("ascii")

    def _document_octets(self):
        self.ensure_one()
        if not self.diagram_ids:
            raise UserError(_(
                "« %s » n'a encore aucun niveau : il n'y a pas de carte à"
                " mettre dans un document.") % self.name)
        from ..generateur import document as gen_doc
        return gen_doc.to_document(self.to_dicts(), self._document_meta(),
                                   self._document_sections())

    @refus_lisible
    def action_telecharger_document(self):
        return self._telecharger(self._document_octets(), "pdf",
                                 "application/pdf", suffixe="-document")
