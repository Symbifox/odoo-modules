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
from markupsafe import Markup

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
    _description = "Import a timeline"

    plan_id = fields.Many2one(
        "bf.gantt.plan", string="Target timeline",
        help="Left empty, a new timeline is created with the file's name.",
    )
    fichier = fields.Binary(string="File", required=True, attachment=False)
    nom_fichier = fields.Char(string="File name")
    format_detecte = fields.Selection(
        [("mspdi", "MS Project (MSPDI .xml)"), ("xlsx", "Workbook (.xlsx)")],
        string="Format", readonly=True,
    )
    remplacer = fields.Boolean(
        string="Replace existing lines", default=False,
        help="Unchecked, the file's lines are added to those already there.",
    )
    state = fields.Selection(
        [("choix", "File selection"), ("apercu", "Preview")],
        default="choix", required=True,
    )
    resume = fields.Html(string="What the file contains", readonly=True)
    ligne_ids = fields.One2many(
        "bf.gantt.import.line", "wizard_id", string="Lines read", readonly=True)

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
            raise UserError(_("No file."))
        try:
            contenu = base64.b64decode(self.fichier)
        except (binascii.Error, ValueError) as erreur:
            raise UserError(_("Unreadable file.")) from erreur

        nom = (self.nom_fichier or "").lower()
        if nom.endswith(".xlsx") or contenu[:2] == b"PK":
            try:
                lignes = gen_xlsx.lire(contenu)
            except Exception as erreur:
                raise UserError(_("Unreadable workbook: %s", erreur)) from erreur
            return lignes, (self.nom_fichier or "").rsplit(".", 1)[0], "xlsx"

        try:
            lu = gen_mspdi.lire(contenu)
        except ValueError as erreur:
            raise UserError(_(
                "Neither a workbook nor an MSPDI file. "
                "%(detail)s\n\nReminder: the binary .mpp is not read. In "
                "Project, use \"Save As\" then \"XML\".",
                detail=self._motif_mspdi(erreur))) from erreur
        return lu["lines"], lu["title"], "mspdi"

    def _motif_mspdi(self, erreur):
        """Le refus du lecteur MSPDI, dans la langue de l'usager.

        `generateur/mspdi.py` tourne hors d'Odoo : il dit POURQUOI il refuse
        (`motif`, `detail`), et c'est ici, où l'env existe, qu'on le dit à
        l'usager. Une autre `ValueError` garde son message tel quel.
        """
        motif = getattr(erreur, "motif", None)
        if motif == "racine":
            return _("This file is not an MSPDI file: the root is "
                     "\"%(root)s\", not \"Project\".", root=erreur.detail)
        if motif == "xml":
            return _("Unreadable XML file: %(error)s", error=erreur.detail)
        return str(erreur)

    def _resume(self, titre, lignes, retenues, format_):
        """L'aperçu, dans la langue de l'usager.

        Une phrase par `_()`, et le balisage reste HORS des termes : il est posé
        par `Markup`, qui échappe tout ce qu'on y insère. Un traducteur ne peut
        donc pas casser la page, et le titre du fichier ne peut rien y injecter.
        """
        sans_date = len(lignes) - retenues
        jalons = sum(1 for l in lignes if l.get("is_milestone"))
        liens = sum(len(l.get("depends_on") or []) for l in lignes)
        couloirs = len({l.get("lane") or "" for l in lignes})
        nombre = Markup("<b>%s</b>") % len(lignes)
        if format_ == "xlsx":
            lues = _("%(count)s lines read from a workbook.", count=nombre)
        else:
            lues = _("%(count)s lines read from an MS Project file.",
                     count=nombre)
        morceaux = [
            Markup("<p>%s</p><ul>") % lues,
            Markup("<li>%s</li>") % _(
                "%(lanes)s lanes, %(milestones)s milestones, %(links)s "
                "dependency links", lanes=couloirs, milestones=jalons,
                links=liens),
        ]
        if sans_date:
            morceaux.append(Markup("<li><b>%s</b></li>") % _(
                "%(count)s lines without a start date will be ignored.",
                count=sans_date))
        if titre:
            # ⚠️ `titre` vient du fichier téléversé. `fields.Html` assainit à
            # l'écriture, mais compter là-dessus revient à publier du HTML
            # étranger et à espérer : `Markup %` l'échappe à la source.
            morceaux.append(Markup("<li>%s</li>") % _(
                "File title: %(title)s", title=titre))
        morceaux.append(Markup("</ul>"))
        if format_ == "mspdi":
            morceaux.append(Markup("<p>%s %s</p>") % (
                _("Project summary tasks become lanes, not lines."),
                _("Resources and assignments are not carried over: this "
                  "module does not schedule people.")))
        return Markup("").join(morceaux)

    # ----------------------------------------------------------------- écrire

    def action_ecrire(self):
        self.ensure_one()
        retenues = self.ligne_ids.filtered("retenue")
        if not retenues:
            raise UserError(_("No line selected."))

        plan = self.plan_id
        if not plan:
            plan = self.env["bf.gantt.plan"].create({
                "name": (self.nom_fichier or _("Imported timeline")).rsplit(
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
            "%(nombre)s lines imported from \"%(fichier)s\".",
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
    _description = "Line read during a timeline import"
    _order = "date_start, id"

    wizard_id = fields.Many2one("bf.gantt.import", required=True, ondelete="cascade")
    retenue = fields.Boolean(string="Keep", default=True)
    name = fields.Char(string="Line", required=True)
    lane = fields.Char(string="Lane")
    assignee = fields.Char(string="Assignee")
    date_start = fields.Date(string="Start")
    date_end = fields.Date(string="End")
    progress = fields.Integer(string="Progress %")
    is_milestone = fields.Boolean(string="Milestone")
    allocated_hours = fields.Float(string="Hours")
    depends_on_names = fields.Char(string="Predecessors")
