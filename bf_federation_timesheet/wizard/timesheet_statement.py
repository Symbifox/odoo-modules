"""Le relevé des heures d'une période, remis comme un livrable fédéré.

Ce module ne fait traverser aucune ligne de temps : il produit un relevé, arrêté,
et le confie à `bf_federation_document`, qui sait déjà remettre une chose datée,
versionnée, et en rapporter l'accusé de réception.

Deux choix valent d'être sus avant de toucher à ce fichier.

**Un relevé est identifié par son projet et sa période, pas par sa date de
production.** Refaire le relevé de septembre ne crée pas un second livrable : il
retrouve le premier par sa référence, en publie une nouvelle version si le contenu
a changé, et c'est le livrable qui fait tomber l'accusé de la version précédente
des deux côtés. Un relevé identique ne change rien, ni version ni accusé.

🔴 **Aucun montant ne sort d'ici.** `account.analytic.line` porte `amount`, le coût
de la ligne, à côté de `unit_amount`, sa durée. Le relevé ne lit que la durée, et
jamais le solde d'une banque d'heures : entre deux entreprises, le temps fait est
une information, la marge ne l'est pas, et chez certains clients la banque
d'heures ne se montre qu'aux conditions convenues avec eux.
"""

import csv
import hashlib
import io
from collections import OrderedDict

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

REGROUPEMENTS = [("task", "Par tâche"), ("employee", "Par personne"), ("day", "Par jour")]


class FederationTimesheetStatement(models.TransientModel):
    _name = "federation.timesheet.statement"
    _description = "Relevé des heures à remettre"

    project_id = fields.Many2one("project.project", string="Projet", required=True)
    partner_id = fields.Many2one(related="project_id.partner_id", string="Destinataire")
    date_from = fields.Date(string="Du", required=True)
    date_to = fields.Date(string="Au", required=True)
    group_by = fields.Selection(REGROUPEMENTS, string="Regrouper", default="task", required=True)
    include_descriptions = fields.Boolean(
        string="Inclure les descriptions", default=True,
        help="Les descriptions des lignes de temps disent ce qui a été fait. Décochez si "
             "elles portent des mentions internes qui n'ont pas à sortir.")
    federation_peer_id = fields.Many2one(
        "federation.peer", string="Remettre à",
        domain="[('id', 'in', allowed_peer_ids)]",
        help="Le pair qui reçoit le relevé. Vide : le livrable est préparé ici, sans être remis.")
    allowed_peer_ids = fields.Many2many("federation.peer", compute="_compute_allowed_peer_ids")
    line_count = fields.Integer(string="Lignes", compute="_compute_apercu")
    total_hours = fields.Float(string="Heures", compute="_compute_apercu")

    @api.depends("project_id", "project_id.partner_id")
    def _compute_allowed_peer_ids(self):
        Peer = self.env["federation.peer"]
        for wiz in self:
            wiz.allowed_peer_ids = Peer._for_partner(wiz.project_id.partner_id) if wiz.project_id.partner_id else Peer

    @api.depends("project_id", "date_from", "date_to")
    def _compute_apercu(self):
        for wiz in self:
            # Un aperçu ne lève pas : la date de fin se tape souvent avant celle de début.
            pret = wiz.project_id and wiz.date_from and wiz.date_to and wiz.date_to >= wiz.date_from
            lignes = wiz._lines() if pret else []
            wiz.line_count = len(lignes)
            wiz.total_hours = sum(l.unit_amount for l in lignes)

    # --- Ce que le relevé lit ----------------------------------------------------------
    def _lines(self):
        self.ensure_one()
        if self.date_to < self.date_from:
            raise UserError(_("La période finit avant de commencer."))
        return self.env["account.analytic.line"].search([
            ("project_id", "=", self.project_id.id),
            ("employee_id", "!=", False),
            ("date", ">=", self.date_from),
            ("date", "<=", self.date_to),
        ], order="date, id")

    def _group_key(self, line):
        if self.group_by == "employee":
            return line.employee_id.name or _("(sans personne)")
        if self.group_by == "day":
            return fields.Date.to_string(line.date)
        return line.task_id.name or _("(sans tâche)")

    def _statement(self):
        """Le relevé, en données : ce que le PDF, le CSV et l'empreinte lisent tous les trois.

        ⚠️ Seules la date, la personne, la tâche, la description et la DURÉE sortent.
        `amount` (le coût) n'est jamais lu, et aucune donnée de banque d'heures non plus.
        """
        self.ensure_one()
        groupes = OrderedDict()
        total = 0.0
        for line in self._lines():
            heures = round(line.unit_amount or 0.0, 2)
            total += heures
            groupe = groupes.setdefault(self._group_key(line), {"lines": [], "hours": 0.0})
            groupe["hours"] = round(groupe["hours"] + heures, 2)
            groupe["lines"].append({
                "date": fields.Date.to_string(line.date),
                "employee": line.employee_id.name or "",
                "task": line.task_id.name or "",
                "description": (line.name or "").strip() if self.include_descriptions else "",
                "hours": heures,
            })
        return {
            "project": self.project_id.display_name,
            "partner": self.project_id.partner_id.display_name or "",
            "company": self.project_id.company_id.name or self.env.company.name,
            "date_from": fields.Date.to_string(self.date_from),
            "date_to": fields.Date.to_string(self.date_to),
            "group_by": dict(REGROUPEMENTS)[self.group_by],
            "include_descriptions": self.include_descriptions,
            "groups": [{"label": k, **v} for k, v in groupes.items()],
            "line_count": sum(len(g["lines"]) for g in groupes.values()),
            "total_hours": round(total, 2),
        }

    @staticmethod
    def _fingerprint(statement):
        """Ce qui décrit le contenu du relevé, jamais le moment où il a été produit."""
        stable = {k: v for k, v in statement.items() if k not in ("company",)}
        brut = repr(sorted(stable.items())).encode("utf-8")
        return hashlib.sha256(brut).hexdigest()

    def _csv(self, statement):
        tampon = io.StringIO()
        ecrit = csv.writer(tampon, delimiter=";")
        ecrit.writerow([_("Date"), _("Personne"), _("Tâche"), _("Description"), _("Heures")])
        for groupe in statement["groups"]:
            for l in groupe["lines"]:
                ecrit.writerow([l["date"], l["employee"], l["task"], l["description"],
                                f"{l['hours']:.2f}".replace(".", ",")])
        ecrit.writerow(["", "", "", _("Total"), f"{statement['total_hours']:.2f}".replace(".", ",")])
        # Le BOM fait ouvrir le fichier en UTF-8 par un tableur, accents compris.
        return ("\ufeff" + tampon.getvalue()).encode("utf-8")

    def _reference(self):
        self.ensure_one()
        return "FDT-%s-%s-%s" % (self.project_id.id, self.date_from.strftime("%Y%m%d"),
                                 self.date_to.strftime("%Y%m%d"))

    # --- Produire et remettre ---------------------------------------------------------
    def _check_role(self):
        if not self.env.su and not self.env.user.has_group("project.group_project_manager"):
            raise AccessError(_("Remettre un relevé des heures demande le rôle de gestionnaire de projet."))

    def action_produce(self):
        """Produire le relevé, le déposer en livrable, et le remettre si un pair est choisi."""
        self.ensure_one()
        self._check_role()
        if not self.project_id.partner_id:
            raise UserError(_("Le projet n'a pas de client : un relevé est remis à quelqu'un."))
        statement = self._statement()
        if not statement["line_count"]:
            raise UserError(_("Aucune heure sur ce projet pour cette période."))
        document = self._produce_document(statement)
        if self.federation_peer_id and document.federation_peer_id != self.federation_peer_id:
            document.write({"federation_peer_id": self.federation_peer_id.id})
        return {
            "type": "ir.actions.act_window", "res_model": "federation.document",
            "res_id": document.id, "view_mode": "form", "target": "current",
        }

    def _produce_document(self, statement):
        self.ensure_one()
        Document = self.env["federation.document"]
        reference = self._reference()
        empreinte = self._fingerprint(statement)
        # 🔴 La référence seule ne suffit pas : un relevé REÇU d'un pair porte la référence
        # que son émetteur lui a donnée, et les identifiants de projet se recoupent d'une
        # instance à l'autre. Chercher sur la seule référence faisait publier une nouvelle
        # version d'un livrable reçu, qui n'est pas le nôtre. On cherche donc un livrable
        # d'ici, tiré de CE projet.
        # ⚠️ Pas de `("federation_origin", "!=", "remote")` dans le domaine : la recherche du
        # socle ne rend que les objets qui ONT un lien, et un relevé jamais remis n'en a pas.
        # Le filtre sur l'origine se fait donc après, en Python.
        document = Document.search([("reference", "=", reference),
                                    ("source_ref", "=", "project.project,%s" % self.project_id.id),
                                    ("company_id", "=", self.project_id.company_id.id or self.env.company.id)],
                                   order="id").filtered(lambda d: d.federation_origin != "remote")[:1]
        if document and document.federation_statement_fingerprint == empreinte:
            return document
        pieces = self._attachments(statement, document)
        vals = {
            "name": _("Relevé des heures, %s, du %s au %s")
                    % (statement["project"], statement["date_from"], statement["date_to"]),
            "reference": reference,
            "issued_on": fields.Date.context_today(self),
            "summary": _("%(n)s ligne(s), %(h)s h, du %(du)s au %(au)s, regroupées %(par)s. "
                         "Le relevé dit combien de temps, jamais combien d'argent.")
                       % {"n": statement["line_count"], "h": f"{statement['total_hours']:.2f}".replace(".", ","),
                          "du": statement["date_from"], "au": statement["date_to"],
                          "par": statement["group_by"].lower()},
            "peer_partner_id": self.project_id.partner_id.id,
            "source_ref": "project.project,%s" % self.project_id.id,
            "federation_statement_fingerprint": empreinte,
            "attachment_ids": [(6, 0, pieces.ids)],
        }
        if document:
            vals["version"] = self._next_version(document.version)
            anciennes = document.attachment_ids
            document.write(vals)
            (anciennes - pieces).unlink()
        else:
            vals["version"] = "1.0"
            document = Document.create(vals)
        pieces.write({"res_model": "federation.document", "res_id": document.id})
        return document

    @staticmethod
    def _next_version(version):
        try:
            majeure, mineure = (version or "1.0").split(".", 1)
            return "%s.%s" % (int(majeure), int(mineure) + 1)
        except ValueError:
            return "%s.1" % (version or "1")

    def _attachments(self, statement, document):
        self.ensure_one()
        Attachment = self.env["ir.attachment"]
        base = "Releve-heures-%s-%s-au-%s" % (self.project_id.id, statement["date_from"], statement["date_to"])
        rapport = self.env.ref("bf_federation_timesheet.action_report_timesheet_statement")
        contenu, _format = self.env["ir.actions.report"]._render_qweb_pdf(
            rapport, res_ids=self.ids, data={"statement": statement})
        # En mode essai, Odoo rend le rapport en HTML plutôt qu'en PDF : le nom et le type
        # suivent ce qui a vraiment été produit, jamais ce qu'on espérait.
        est_pdf = contenu[:4] == b"%PDF"
        pdf = Attachment.create({
            "name": base + (".pdf" if est_pdf else ".html"),
            "mimetype": "application/pdf" if est_pdf else "text/html",
            "raw": contenu,
            "res_model": "federation.document", "res_id": document.id if document else 0,
        })
        tableur = Attachment.create({
            "name": base + ".csv", "mimetype": "text/csv", "raw": self._csv(statement),
            "res_model": "federation.document", "res_id": document.id if document else 0,
        })
        return pdf | tableur
