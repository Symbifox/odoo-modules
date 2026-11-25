from odoo import _, api, fields, models


class TrainingRecord(models.Model):
    _inherit = "bf.training.record"

    event_id = fields.Many2one(
        "event.event", string="Session", index=True, ondelete="set null",
        help="The in-class session this completion comes from.")
    #: ⚠️ Écrit par le pont à la création, PAS calculé. Un calcul qui cherche
    #: l'inscription pointant vers cette réalisation rendrait toujours faux : le
    #: lien retour n'existe qu'APRÈS la création, et rien ne le ferait recalculer.
    event_registration_id = fields.Many2one(
        "event.registration", string="Registration", readonly=True, copy=False,
        ondelete="set null")
    signature_manquante = fields.Boolean(
        string="Missing signature", compute="_compute_signature_manquante",
        help="The session requires a signed sheet and this person has not "
             "signed.")

    @api.depends("event_id", "event_registration_id.training_signature")
    def _compute_signature_manquante(self):
        for rec in self:
            inscription = rec.event_registration_id
            rec.signature_manquante = bool(
                rec.event_id
                and rec.event_id.training_requires_signature
                and inscription
                and not inscription.training_signature)

    def _compute_is_complete(self):
        """Une présence non signée est incomplète, jamais absente.

        🔴 Le pont **écrit** la réalisation même sans signature : la personne
        était là, et perdre sa présence pour une signature manquante serait pire
        que de la consigner imparfaitement. Ce qui manque est nommé, comme pour
        tout le reste du socle.

        ⚠️ On repart de `rec.missing_info` calculé par le socle au lieu de
        l'écraser : sans ça, « les heures » disparaîtrait dès qu'il manque aussi
        la signature, et la personne corrigerait un manque à la fois sans jamais
        voir la liste.
        """
        super()._compute_is_complete()
        for rec in self:
            if not rec.event_id:
                continue
            manques = [rec.missing_info] if rec.missing_info else []
            if rec.signature_manquante:
                manques.append(_("the learner's signature"))
            if manques:
                rec.is_complete = False
                rec.missing_info = ", ".join(manques)
