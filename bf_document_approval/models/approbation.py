# -*- coding: utf-8 -*-
"""Qui devait se prononcer, qui l'a fait, et ce qui en découle.

Le besoin, tel qu'il a été formulé : « Monsieur Tremblay a décidé ça, mais
Madame Couture finalement a décidé que ça serait telle affaire ; ils ont voté,
ça a été approuvé. Puis quand c'est approuvé, les gens qui sont susceptibles
d'être touchés reçoivent une copie automatiquement. »

Trois choses, donc : plusieurs avis, un verrou tant qu'ils manquent, et une
diffusion qui part toute seule vers les bonnes personnes. La troisième existait
déjà — `project.document.distribution` sait accuser réception, exiger une
signature et se marquer périmée. Ce fichier ne réécrit rien de tout ça : il
nomme les approbateurs, il bloque, et il branche la diffusion sur le RACI qui
vit déjà dans la matrice.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError

AVIS = [
    ("attente", "En attente"),
    ("approuve", "Approuvé"),
    ("refuse", "Refusé"),
]


class ProjectDocumentApprover(models.Model):
    """L'avis d'une personne sur une version de document."""

    _name = "project.document.approver"
    _description = "Approbateur d'une version de document"
    _order = "version_id, sequence, id"

    version_id = fields.Many2one(
        "project.document.version", string="Version", required=True,
        ondelete="cascade", index=True)
    document_id = fields.Many2one(
        "project.document", related="version_id.document_id", store=True,
        string="Document", index=True)
    sequence = fields.Integer(string="Ordre", default=10)
    user_id = fields.Many2one(
        "res.users", string="Approbateur", required=True,
        help="La personne qui doit se prononcer, pas celle qui rédige.")
    requis = fields.Boolean(
        string="Requis", default=True,
        help="Décoché, l'avis est sollicité mais ne bloque pas la publication.")
    avis = fields.Selection(AVIS, string="Avis", default="attente",
                            required=True)
    date_avis = fields.Datetime(string="Date de l'avis", readonly=True)
    commentaire = fields.Char(
        string="Motif",
        help="Obligatoire pour un refus : un refus sans motif ne se traite pas.")

    _sql_constraints = [
        ("un_avis_par_personne", "unique (version_id, user_id)",
         "Cette personne se prononce déjà sur cette version."),
    ]

    def _poser(self, avis, commentaire=None):
        self.ensure_one()
        if avis == "refuse" and not (commentaire or self.commentaire):
            raise UserError(_(
                "Un refus sans motif ne se traite pas : dites ce qui doit"
                " changer."))
        vals = {"avis": avis, "date_avis": fields.Datetime.now()}
        if commentaire:
            vals["commentaire"] = commentaire
        self.write(vals)
        self.version_id.document_id.message_post(body=_(
            "%(qui)s a %(quoi)s la version %(version)s.%(motif)s",
            qui=self.user_id.name,
            quoi=_("approuvé") if avis == "approuve" else _("refusé"),
            version=self.version_id.version_number or "",
            motif=(" %s" % (commentaire or self.commentaire))
            if (commentaire or self.commentaire) else ""))
        return True

    def action_approuver(self):
        return self._poser("approuve")

    def action_refuser(self):
        return self._poser("refuse")


class ProjectDocumentVersionApprobation(models.Model):
    _inherit = "project.document.version"

    approver_ids = fields.One2many(
        "project.document.approver", "version_id", string="Approbateurs")
    approbation_attendue_count = fields.Integer(
        string="Avis attendus", compute="_compute_approbation")
    approbation_refusee = fields.Boolean(
        string="Refusée", compute="_compute_approbation")
    approbation_complete = fields.Boolean(
        string="Approbation complète", compute="_compute_approbation",
        help="Aucun avis requis ne manque, et aucun n'est un refus.")

    @api.depends("approver_ids.avis", "approver_ids.requis")
    def _compute_approbation(self):
        for rec in self:
            requis = rec.approver_ids.filtered("requis")
            manquants = requis.filtered(lambda a: a.avis == "attente")
            rec.approbation_attendue_count = len(manquants)
            rec.approbation_refusee = bool(
                rec.approver_ids.filtered(lambda a: a.avis == "refuse"))
            rec.approbation_complete = bool(
                not manquants and not rec.approbation_refusee)

    def _exiger_approbation(self, geste):
        """Refuse le geste tant que le tour de table n'est pas fini.

        Sans approbateur nommé, rien ne change : c'est le comportement d'avant,
        et une organisation qui n'a pas de tour de table à faire ne doit pas
        être forcée d'en inventer un.
        """
        for rec in self:
            if not rec.approver_ids.filtered("requis"):
                continue
            if rec.approbation_refusee:
                refus = rec.approver_ids.filtered(lambda a: a.avis == "refuse")
                raise UserError(_(
                    "%(geste)s est refusé : %(qui)s a rejeté cette version.\n\n"
                    "%(motifs)s",
                    geste=geste, qui=", ".join(refus.mapped("user_id.name")),
                    motifs="\n".join(
                        "· %s" % (a.commentaire or _("sans motif"))
                        for a in refus)))
            if rec.approbation_attendue_count:
                attente = rec.approver_ids.filtered(
                    lambda a: a.requis and a.avis == "attente")
                raise UserError(_(
                    "%(geste)s est refusé : %(n)s avis manquent encore.\n\n"
                    "En attente de %(qui)s.",
                    geste=geste, n=rec.approbation_attendue_count,
                    qui=", ".join(attente.mapped("user_id.name"))))

    def action_approve(self):
        self._exiger_approbation(_("Approuver cette version"))
        return super().action_approve()

    def action_release(self):
        # ⚠️ `action_release` du module hôte approuve d'office quand personne
        # ne l'a fait. Une politique pouvait donc être publiée d'un clic sans
        # qu'aucun des approbateurs nommés se soit prononcé : c'est ce chemin-là
        # qu'il faut fermer, pas seulement `action_approve`.
        self._exiger_approbation(_("Publier cette version"))
        return super().action_release()

    # ------------------------------------------------------------ diffusion
    def _partenaires_informes(self):
        """Les parties prenantes à informer, d'après la matrice du document.

        On ne tient pas une deuxième liste de destinataires : le RACI vit dans
        les éléments de matrice, et une liste recopiée est une liste qui se
        périme.
        """
        self.ensure_one()
        matrice = self.document_id.matrix_id
        if not matrice:
            return self.env["res.partner"].browse()
        elements = self.env["project.knowledge.item"].search(
            [("matrix_id", "=", matrice.id)])
        return elements.mapped("stakeholder_informed_ids")

    def action_diffuser_aux_informes(self):
        """Crée les distributions manquantes vers les personnes à informer."""
        self.ensure_one()
        if self.state not in ("approved", "released"):
            raise UserError(_(
                "On ne diffuse pas une version qui n'est pas approuvée."))
        Distribution = self.env["project.document.distribution"]
        deja = Distribution.search(
            [("version_id", "=", self.id)]).mapped("partner_id")
        cibles = self._partenaires_informes() - deja
        if not cibles:
            raise UserError(_(
                "Personne de neuf à informer : soit la matrice ne désigne"
                " aucune partie prenante informée, soit elles ont toutes déjà"
                " reçu cette version."))
        creees = Distribution.create([{
            "version_id": self.id,
            "recipient_type": "partner",
            "partner_id": partenaire.id,
            "distribution_method": "email",
        } for partenaire in cibles])
        self.document_id.message_post(body=_(
            "Version %(version)s diffusée à %(n)s partie(s) prenante(s)"
            " informée(s) : %(qui)s.",
            version=self.version_number or "", n=len(creees),
            qui=", ".join(cibles.mapped("display_name"))))
        return {
            "type": "ir.actions.act_window",
            "name": _("Distributions — version %s") % (self.version_number or ""),
            "res_model": "project.document.distribution",
            "view_mode": "list,form",
            "domain": [("id", "in", creees.ids)],
        }
