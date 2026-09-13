import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class ProjectDocumentDistribution(models.Model):
    """Un accusé qui vaut preuve écrit une ligne au registre.

    ⚠️ **Et un accusé qui ne vaut pas preuve n'en écrit pas.** Quand la remise
    exige une signature et qu'elle manque, rien n'est consigné : une case cochée
    ne remplace pas un document daté et signé, et c'est précisément ce que la
    réglementation demande de verser au dossier.
    """

    _inherit = "project.document.distribution"

    training_record_id = fields.Many2one(
        "bf.training.record", string="Réalisation au registre",
        ondelete="set null", readonly=True)
    training_activity_id = fields.Many2one(
        "bf.training.activity", string="Activité du registre",
        compute="_compute_training_activity_id")

    @api.depends("document_id")
    def _compute_training_activity_id(self):
        Activite = self.env["bf.training.activity"]
        for rec in self:
            rec.training_activity_id = Activite.search(
                [("document_id", "=", rec.document_id.id)], limit=1
            ) if rec.document_id else False

    def _vaut_preuve(self):
        """L'accusé est-il complet au sens de ce qu'on doit conserver ?"""
        self.ensure_one()
        if self.state != "acknowledged":
            return False
        if self.requires_signature and not self.signature_date:
            return False
        return True

    def _ecrire_au_registre(self):
        """Crée la réalisation correspondant à cet accusé, une seule fois."""
        Realisation = self.env["bf.training.record"].sudo()
        Employe = self.env["hr.employee"].sudo()
        creees = Realisation.browse()
        for remise in self:
            if remise.training_record_id or not remise._vaut_preuve():
                continue
            activite = remise.training_activity_id
            if not activite:
                continue
            partenaire = remise.partner_id or remise.user_id.partner_id
            employe = Employe.search([
                "|", ("work_contact_id", "=", partenaire.id),
                ("user_id.partner_id", "=", partenaire.id),
            ], limit=1)
            if not employe:
                _logger.info(
                    "Registre de formation : accusé de la remise %s sans fiche "
                    "d'employé ; rien écrit au registre.", remise.id)
                continue
            jour = (remise.acknowledged_date or fields.Datetime.now()).date()
            realisation = Realisation.create({
                "employee_id": employe.id,
                "activity_id": activite.id,
                "date_done": jour,
                "hours": activite.duration_hours,
                "mode": activite.mode,
                "state": "confirmed",
                "distribution_id": remise.id,
                "document_version_id": remise.version_id.id,
                "note": _("Écrite automatiquement à l'accusé de réception."),
            })
            remise.training_record_id = realisation
            creees |= realisation
        return creees

    def action_acknowledge(self):
        resultat = super().action_acknowledge()
        self._ecrire_au_registre()
        return resultat

    def write(self, vals):
        """Une signature qui arrive APRÈS l'accusé débloque l'écriture.

        ⚠️ Sans ce crochet, une remise accusée puis signée resterait sans ligne
        au registre : `action_acknowledge` était déjà passée, et rien ne
        repasserait jamais.
        """
        resultat = super().write(vals)
        if "signature_date" in vals or "state" in vals:
            self._ecrire_au_registre()
        return resultat
