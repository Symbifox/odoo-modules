import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class EventRegistration(models.Model):
    _inherit = "event.registration"

    training_activity_id = fields.Many2one(
        related="event_id.training_activity_id", store=True, index=True,
        string="Activité de formation")
    training_signature = fields.Binary(
        string="Signature", attachment=True,
        help="La signature de la personne sur la feuille de présence.")
    training_signature_date = fields.Datetime(
        string="Signée le", readonly=True,
        help="Écrit au moment où la signature est apposée. Ce n'est pas la date "
             "de la séance : c'est la date de la signature.")
    training_record_id = fields.Many2one(
        "bf.training.record", string="Réalisation", readonly=True, copy=False,
        help="La ligne du registre écrite pour cette présence.")

    def write(self, vals):
        """Horodater la signature, et écrire au registre quand la présence tombe.

        ⚠️ Les deux gestes arrivent dans n'importe quel ordre : on signe la
        feuille puis on pointe, ou on pointe puis on fait signer. Un seul point
        d'entrée en manquerait la moitié.
        """
        if vals.get("training_signature") and not vals.get("training_signature_date"):
            vals["training_signature_date"] = fields.Datetime.now()
        resultat = super().write(vals)
        if "state" in vals or "training_signature" in vals:
            self._ecrire_au_registre()
        return resultat

    def _vaut_presence(self):
        """Cette inscription atteste-t-elle une présence ?

        🔴 On lit l'ÉTAT, jamais `date_closed`. Le calcul de `date_closed` est
        gardé par `if not registration.date_closed` : une fois la valeur posée,
        rien ne l'efface. Une inscription marquée présente puis ANNULÉE garde
        donc sa « date de présence », et la lire seule fait compter des présents
        qui ne sont pas venus.
        """
        self.ensure_one()
        return self.state == "done"

    def _employe(self):
        self.ensure_one()
        partenaire = self.partner_id
        if not partenaire:
            return self.env["hr.employee"].browse()
        return self.env["hr.employee"].sudo().search([
            "|", ("work_contact_id", "=", partenaire.id),
            ("user_id.partner_id", "=", partenaire.id),
        ], limit=1)

    def _ecrire_au_registre(self):
        """Créer la réalisation de chaque présence, une seule fois."""
        Realisation = self.env["bf.training.record"].sudo()
        creees = Realisation.browse()
        for inscription in self:
            if inscription.training_record_id or not inscription._vaut_presence():
                continue
            activite = inscription.training_activity_id
            if not activite:
                continue
            employe = inscription._employe()
            if not employe:
                _logger.info(
                    "Registre de formation : présence %s sans fiche d'employé ; "
                    "rien écrit au registre.", inscription.id)
                continue
            jour = inscription.event_id._jour_de_la_seance()
            if not jour:
                _logger.info(
                    "Registre de formation : séance %s sans date de début ; "
                    "rien écrit au registre.", inscription.event_id.id)
                continue
            realisation = Realisation.create({
                "employee_id": employe.id,
                "activity_id": activite.id,
                "date_done": jour,
                "hours": inscription.event_id.training_hours,
                "mode": activite.mode,
                "state": "confirmed",
                "event_id": inscription.event_id.id,
                "event_registration_id": inscription.id,
                "note": _("Écrite automatiquement à la présence en séance."),
            })
            inscription.training_record_id = realisation
            creees |= realisation
        return creees
