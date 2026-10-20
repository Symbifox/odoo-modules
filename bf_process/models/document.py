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
    ("couverture", "Cover block"),
    ("hypothese", "Assumption"),
    ("question", "Open question"),
    ("constat", "Finding"),
    ("annexe", "Appendix page"),
    ("validation", "Validation register line"),
]

# Ce qui se regroupe sur une même page d'annexe, et dans quel ordre.
PAGES = (
    ("hypothese", "Assumptions made to draw the map"),
    ("question", "Open questions"),
    ("constat", "What the map reveals"),
)


class BfProcessSection(models.Model):
    _name = "bf.process.section"
    _inherit = ["bf.process.gel"]
    _description = "Process map deliverable section"
    _order = "kind, sequence, id"

    process_id = fields.Many2one(
        "bf.process", string="Process map", required=True,
        ondelete="cascade", index=True)
    sequence = fields.Integer(string="Order", default=10)
    kind = fields.Selection(GENRES, string="Kind", required=True,
                            default="hypothese")
    name = fields.Char(
        string="Heading", required=True,
        help="The block title, the statement of the assumption, or the "
             "role expected in the validation register.")
    body = fields.Html(
        string="Body", sanitize=True,
        help="The section text. For a register line, the name of the "
             "expected person.")

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
                    "\"%s\" has no body. An empty section gets numbered "
                    "and reads as an omission: write it, or remove it."
                ) % rec.name)


class BfProcessDocument(models.Model):
    _inherit = "bf.process"

    section_ids = fields.One2many(
        "bf.process.section", "process_id", string="Deliverable sections")
    section_count = fields.Integer(compute="_compute_section_count")
    sous_titre = fields.Char(
        string="Document subtitle",
        help="The line under the title, on the cover. For example: "
             "\"current state (AS-IS), from the opening of the fiscal "
             "year to the financial statements\".")
    pied_document = fields.Char(
        string="Document footer",
        help="What the bottom of each page says: where the map comes "
             "from, and what was not done. If empty, the first line of "
             "the source is used.")
    date_document = fields.Date(
        string="Document date", default=fields.Date.context_today)

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
            "name": _("Deliverable sections"),
            "res_model": "bf.process.section",
            "view_mode": "list,form",
            "domain": [("process_id", "=", self.id)],
            "context": {"default_process_id": self.id},
        }

    # ------------------------------------------------------------- livrable --
    def _document_meta(self):
        """Ce que la mise en page a besoin de savoir, et rien de plus.

        Dans la langue du livrable (celle du client), pas dans celle de qui
        clique sur « Livrable PDF ».
        """
        self.ensure_one()
        env = self._livrable().env
        client = self.partner_id.display_name or self.pool_name or ""
        date = fields.Date.to_string(self.date_document) if self.date_document else ""
        # « Brouillon » sur la couverture d'un livrable client se lit comme un
        # défaut de la carte, alors qu'il ne dit que l'état du modèle. Le
        # document d'un client disait « projet, à valider » : la même chose, mais
        # adressée au lecteur.
        etat = {"brouillon": env._("draft, to be validated"),
                "valide": env._("validated"),
                "obsolete": env._("obsolete version")}.get(self.state, "")
        # Le bandeau disait « (AS-IS) » en dur. Sur une carte du processus
        # souhaité, c'était faux à chaque page : le lecteur aurait pris la
        # cible pour un relevé de ce qui se fait aujourd'hui, ce qui est
        # exactement l'inverse de ce que le document raconte.
        genre = (env._("Process map (AS-IS)") if self.nature == "actuel"
                 else env._("Target process (TO-BE)"))
        return {
            "titre": self.name,
            "sous_titre": self.sous_titre or client,
            "ligne_source": " · ".join(x for x in (
                client, "v%s" % self.version, etat, date) if x),
            "droite": " · ".join(x for x in (
                client, genre, "v%s" % self.version) if x),
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

    def _document_plan(self):
        """Le plan de transformation, mis en mots pour l'impression.

        Les écarts mis de côté n'y sont pas : un plan dit ce qu'on va faire.
        Les écarts caducs non plus, pour la même raison. Ce qui reste est
        trié comme au tableau, c'est-à-dire par niveau puis par ordre de
        semis, si bien que le document suit la carte.
        """
        self.ensure_one()
        if self.nature != "cible":
            return []
        # les libellés des sélections, lus dans la langue du livrable : le
        # plan imprimé est une pièce du client
        env = self._livrable().env
        champs = env["bf.process.ecart"]._fields
        intentions = dict(champs["intention"]._description_selection(env))
        etats = dict(champs["etat"]._description_selection(env))
        efforts = dict(champs["effort"]._description_selection(env))
        lignes = []
        for e in self.ecart_ids.sorted(
                key=lambda e: (e.diagram_code or "", e.sequence, e.id)):
            if e.etat == "ecarte" or e.caduc:
                continue
            lignes.append({
                "niveau": e.diagram_titre or "",
                "ecart": e.libelle or "",
                "intention": intentions.get(e.intention, ""),
                "gain": e.gain or "",
                "effort": efforts.get(e.effort, ""),
                "responsable": e.responsable_id.name or "",
                "etat": etats.get(e.etat, ""),
            })
        return lignes

    @refus_lisible
    def exporter_document_pdf(self):
        """Le livrable en base64 — la forme qui traverse XML-RPC."""
        self.ensure_one()
        return base64.b64encode(self._document_octets()).decode("ascii")

    def _document_octets(self):
        self.ensure_one()
        if not self.diagram_ids:
            raise UserError(_(
                "\"%s\" has no level yet: there is no map to put in a "
                "document.") % self.name)
        from ..generateur import document as gen_doc
        return gen_doc.to_document(self.to_dicts(), self._document_meta(),
                                   self._document_sections(),
                                   self._document_plan(),
                                   libelles=self._libelles_rendu())

    def _libelles_rendu(self):
        """L'habillage des PDF (livrable, tracé, tirage d'atelier, vue delta).

        Les générateurs sont du Python pur, sans `_()` : ils gardent l'anglais
        par défaut et reçoivent d'ici, dans la langue du livrable, chaque mot
        qu'ils posent autour de la carte. Les clés sont celles de
        `generateur.document.LIBELLES` et `generateur.pdf.LIBELLES`.
        """
        self.ensure_one()
        langue = self._langue_livrable()
        env = self.with_context(lang=langue).env
        return {
            # espacement français (fine insécable avant « ; : ! ? ») : une
            # règle de la langue, pas de la mise en page
            "espaces_fr": langue.startswith("fr"),
            # légende : les coupes de ligne sont faites à la main, par langue
            "legende": {
                "start": env._("Start event,\nnamed by its state"),
                "task": env._("Activity\n(verb + object)"),
                "send": env._("Activity that sends\na message"),
                "receive": env._("Activity that receives\na message"),
                "user": env._("Activity performed\nby a person"),
                "sub": env._("Subprocess\ndetailed further on"),
                "xor": env._("Exclusive choice\n(one path only)"),
                "or": env._("Inclusive choice\n(one or more)"),
                "and": env._("Parallelism\n(all paths)"),
                "msgCatch": env._("Waiting for an\nexternal event"),
                "timerCatch": env._("Waiting for\na deadline"),
                "store": env._("Stored data"),
                "end": env._("End, named by\nthe state reached"),
            },
            "legende_idee": (env._("Improvement or"),
                             env._("automation idea")),
            "legende_fragilite": (env._("Weak point or"),
                                  env._("missing information")),
            "Legend": env._("Legend"),
            "Contents": env._("Contents"),
            "titres_annexes": {
                "hypothese": env._("Assumptions made to draw the map"),
                "question": env._("Open questions"),
                "constat": env._("What the map reveals"),
            },
            "Role": env._("Role"),
            "Name": env._("Name"),
            "Date": env._("Date"),
            "Decision": env._("Decision"),
            "Accurate / needs correction": env._("Accurate / needs correction"),
            "Validation register": env._("Validation register"),
            "registre_intro": env._(
                "An AS-IS map is only worth something once it is recognized "
                "by the people who do the work. Fill in and return with your "
                "corrections."),
            "Level": env._("Level"),
            "Gap": env._("Gap"),
            "Intent": env._("Intent"),
            "Expected gain": env._("Expected gain"),
            "Effort": env._("Effort"),
            "Assignee": env._("Assignee"),
            "State": env._("State"),
            "Appendix": env._("Appendix"),
            "Assumptions and open questions": env._(
                "Assumptions and open questions"),
            "hypotheses_sous_titre": env._(
                "What the map assumes, and what it does not know yet"),
            "Findings and ideas": env._("Findings and ideas"),
            "constats_sous_titre": env._(
                "Reading of the map, with no tool recommendation"),
            "Transformation plan": env._("Transformation plan"),
            " (continued)": env._(" (continued)"),
            "plan_sous_titre": env._(
                "What separates the current state from the target process, "
                "and what each change brings"),
            "Unfolded map": env._("Unfolded map"),
            "Single page": env._("Single page"),
            "%s: unfolded map": env._("%s: unfolded map"),
            "depliee_sous_titre": env._(
                "All levels on a single page, to browse on screen"),
            "Process mapping": env._("Process mapping"),
            # generateur/pdf.py : tracé nu, tirage d'atelier, vue delta
            "Column %d/%d · Row %d/%d": env._("Column %d/%d · Row %d/%d"),
            "Scaled to %d %% to fit on a PDF page.": env._(
                "Scaled to %d %% to fit on a PDF page."),
        }

    @refus_lisible
    def action_telecharger_document(self):
        return self._telecharger(self._document_octets(), "pdf",
                                 "application/pdf", suffixe="-document")
