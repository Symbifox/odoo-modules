# -*- coding: utf-8 -*-
"""L'import, en deux temps : on lit, on montre, puis on écrit.

Un import qui écrit d'abord et explique ensuite est un import qu'on subit. Ici
l'assistant lit le fichier, affiche ce qu'il a compris et ce qu'il a laissé
tomber, et n'écrit qu'au deuxième clic.

Deux formats en entrée : le MSPDI de Microsoft Project (aussi ce qu'OpenProject
sait produire) et le classeur produit par notre propre export.
"""
import base64
import binascii
from markupsafe import escape

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..generateur import mspdi as gen_mspdi
from ..generateur import xlsx as gen_xlsx

STATUT_VERS_ETAT = {
    "done": "done",
    "canceled": "cancel",
    "in_progress": "doing",
    "overdue": "doing",
    "upcoming": "todo",
}


class BfGanttImport(models.TransientModel):
    _name = "bf.gantt.import"
    _description = "Importer un échéancier"

    plan_id = fields.Many2one(
        "bf.gantt.plan", string="Échéancier cible",
        help="Laissé vide, un nouvel échéancier est créé au nom du fichier.",
    )
    fichier = fields.Binary(string="Fichier", required=True, attachment=False)
    nom_fichier = fields.Char(string="Nom du fichier")
    format_detecte = fields.Selection(
        [("mspdi", "MS Project (MSPDI .xml)"), ("xlsx", "Classeur (.xlsx)")],
        string="Format", readonly=True,
    )
    remplacer = fields.Boolean(
        string="Remplacer les lignes existantes", default=False,
        help="Décoché, les lignes du fichier s'ajoutent à celles déjà là.",
    )
    state = fields.Selection(
        [("choix", "Choix du fichier"), ("apercu", "Aperçu")],
        default="choix", required=True,
    )
    resume = fields.Html(string="Ce que le fichier contient", readonly=True)
    ligne_ids = fields.One2many(
        "bf.gantt.import.line", "wizard_id", string="Lignes lues", readonly=True)

    # ------------------------------------------------------------------ lire

    def action_lire(self):
        self.ensure_one()
        lignes, titre, format_ = self._analyser()
        self.ligne_ids.unlink()
        self.env["bf.gantt.import.line"].create([
            {
                "wizard_id": self.id,
                "name": ligne["name"][:255],
                "lane": (ligne.get("lane") or "")[:128],
                "assignee": (ligne.get("assignee") or "")[:128],
                "date_start": ligne.get("start"),
                "date_end": ligne.get("end") or ligne.get("start"),
                "progress": ligne.get("progress") or 0,
                "is_milestone": bool(ligne.get("is_milestone")),
                "allocated_hours": ligne.get("allocated_hours") or 0.0,
                "depends_on_names": " ; ".join(ligne.get("depends_on") or []),
                "retenue": bool(ligne.get("start")),
            }
            for ligne in lignes
        ])
        retenues = len(self.ligne_ids.filtered("retenue"))
        self.write({
            "format_detecte": format_,
            "state": "apercu",
            "resume": self._resume(titre, lignes, retenues, format_),
        })
        return self._rouvrir()

    def _analyser(self):
        if not self.fichier:
            raise UserError(_("Aucun fichier."))
        try:
            contenu = base64.b64decode(self.fichier)
        except (binascii.Error, ValueError) as erreur:
            raise UserError(_("Fichier illisible.")) from erreur

        nom = (self.nom_fichier or "").lower()
        if nom.endswith(".xlsx") or contenu[:2] == b"PK":
            try:
                lignes = gen_xlsx.lire(contenu)
            except Exception as erreur:
                raise UserError(_("Classeur illisible : %s", erreur)) from erreur
            return lignes, (self.nom_fichier or "").rsplit(".", 1)[0], "xlsx"

        try:
            lu = gen_mspdi.lire(contenu)
        except ValueError as erreur:
            raise UserError(_(
                "Ni un classeur, ni un MSPDI. %(detail)s\n\n"
                "Rappel : le .mpp binaire n'est pas lu. Dans Project, "
                "« Enregistrer sous » puis « XML ».", detail=erreur)) from erreur
        return lu["lines"], lu["title"], "mspdi"

    def _resume(self, titre, lignes, retenues, format_):
        sans_date = len(lignes) - retenues
        jalons = sum(1 for l in lignes if l.get("is_milestone"))
        liens = sum(len(l.get("depends_on") or []) for l in lignes)
        couloirs = len({l.get("lane") or "" for l in lignes})
        morceaux = [
            "<p><b>%s</b> lignes lues dans %s." % (
                len(lignes),
                "un classeur" if format_ == "xlsx" else "un fichier MS Project"),
            "</p><ul>",
            "<li>%s couloirs, %s jalons, %s liens de dépendance</li>" % (
                couloirs, jalons, liens),
        ]
        if sans_date:
            morceaux.append(
                "<li><b>%s lignes sans date de début seront ignorées.</b></li>"
                % sans_date)
        if titre:
            # ⚠️ `titre` vient du fichier téléversé. `fields.Html` assainit à
            # l'écriture, mais compter là-dessus revient à publier du HTML
            # étranger et à espérer : on échappe à la source.
            morceaux.append("<li>Titre du fichier : %s</li>" % escape(titre))
        morceaux.append("</ul>")
        if format_ == "mspdi":
            morceaux.append(
                "<p>Les tâches récapitulatives de Project deviennent des "
                "couloirs, pas des lignes. Les ressources et les affectations "
                "ne sont pas reprises : ce module ne planifie pas les gens.</p>")
        return "".join(morceaux)

    # ----------------------------------------------------------------- écrire

    def action_ecrire(self):
        self.ensure_one()
        retenues = self.ligne_ids.filtered("retenue")
        if not retenues:
            raise UserError(_("Aucune ligne retenue."))

        plan = self.plan_id
        if not plan:
            plan = self.env["bf.gantt.plan"].create({
                "name": (self.nom_fichier or _("Échéancier importé")).rsplit(
                    ".", 1)[0][:128],
                "date_start": min(retenues.mapped("date_start")),
                "date_end": max(l.date_end or l.date_start for l in retenues),
            })
        elif self.remplacer:
            plan.item_ids.unlink()

        cree = {}
        sequence = 10
        for ligne in retenues:
            item = self.env["bf.gantt.item"].create({
                "plan_id": plan.id,
                "name": ligne.name,
                "lane": ligne.lane,
                "assignee": ligne.assignee,
                "sequence": sequence,
                "date_start": ligne.date_start,
                "date_end": ligne.date_end or ligne.date_start,
                "progress": ligne.progress,
                "is_milestone": ligne.is_milestone,
                "allocated_hours": ligne.allocated_hours,
                "state": "done" if ligne.progress >= 100 else "todo",
            })
            cree[ligne.name] = item
            sequence += 10

        # Les liens en deuxième passe : une ligne peut citer une ligne d'après.
        for ligne in retenues:
            if not ligne.depends_on_names:
                continue
            amonts = [cree[nom.strip()] for nom in ligne.depends_on_names.split(";")
                      if nom.strip() in cree]
            if amonts:
                cree[ligne.name].depend_on_ids = [(6, 0, [a.id for a in amonts])]

        plan.message_post(body=_(
            "%(nombre)s lignes importées depuis « %(fichier)s ».",
            nombre=len(retenues), fichier=self.nom_fichier or ""))

        return {
            "type": "ir.actions.act_window",
            "res_model": "bf.gantt.plan",
            "res_id": plan.id,
            "view_mode": "form",
            "target": "current",
        }

    def _rouvrir(self):
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }


class BfGanttImportLine(models.TransientModel):
    _name = "bf.gantt.import.line"
    _description = "Ligne lue à l'import d'un échéancier"
    _order = "date_start, id"

    wizard_id = fields.Many2one("bf.gantt.import", required=True, ondelete="cascade")
    retenue = fields.Boolean(string="Retenir", default=True)
    name = fields.Char(string="Ligne", required=True)
    lane = fields.Char(string="Couloir")
    assignee = fields.Char(string="Responsable")
    date_start = fields.Date(string="Début")
    date_end = fields.Date(string="Fin")
    progress = fields.Integer(string="Avancement %")
    is_milestone = fields.Boolean(string="Jalon")
    allocated_hours = fields.Float(string="Heures")
    depends_on_names = fields.Char(string="Précédée par")
