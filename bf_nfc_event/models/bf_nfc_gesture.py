"""Le geste « je suis là » : une présence à la séance en cours.

⚠️ Écrit en sudo : dans Odoo, les inscriptions ne sont ouvertes qu'au comptoir
d'accueil et à la gestion des événements. Noter SA propre présence n'est pas un
privilège d'accueil ; la pastille est la permission, et le geste n'écrit jamais
que l'inscription de la personne qui tape.

🔴 On lit l'ÉTAT de l'inscription, jamais `date_closed` : ce calcul est gardé par
« si vide », une date posée ne s'efface plus, et une inscription présente puis
annulée garderait sa date (même piège que dans le registre de formation).
"""
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

AVANT = timedelta(hours=1)
APRES = timedelta(minutes=30)


class BfNfcGesture(models.Model):
    _inherit = "bf.nfc.gesture"

    kind = fields.Selection(
        selection_add=[("event_presence", "Record my attendance at the session")],
        ondelete={"event_presence": "cascade"},
    )

    @api.model
    def _modeles_supplementaires(self):
        return super()._modeles_supplementaires() + ["event.event"]

    def _executer_event_presence(self, tag, tap, params):
        self.ensure_one()
        self._exiger_une_personne(params)
        # ⚠️ En sudo : dans Odoo, une séance n'est lisible que par les groupes des
        # événements. Un participant qui tape la porte n'en fait pas partie, et la
        # pastille visant la séance est déjà la permission de s'y dire présent.
        cible = tag._cible(superutilisateur=True)
        moment = self._moment(params)
        Evenement = self.env["event.event"].sudo()
        if cible and cible._name == "event.event":
            seances = Evenement.browse(cible.id)
            if not self._seance_en_cours(seances, moment):
                raise UserError(_("“%(seance)s” takes place on %(quand)s: "
                                  "attendance is recorded during the "
                                  "session.", seance=seances.name,
                                  quand=self._heure(seances.date_begin, "%Y-%m-%d %H:%M")))
        elif cible and cible._name == "res.partner":
            seances = Evenement.search([
                ("address_id", "=", cible.id),
                ("date_begin", "<=", moment + AVANT),
                ("date_end", ">=", moment - APRES),
            ], order="date_begin")
            annulee = self.env.ref("event.event_stage_cancelled", raise_if_not_found=False)
            if annulee:
                seances = seances.filtered(lambda s: s.stage_id != annulee)
            if not seances:
                raise UserError(_("No session in progress at “%s”.", cible.display_name))
        else:
            raise UserError(_("This tag must point to a session or a "
                              "session's venue."))

        choix = params.get("choix")
        if len(seances) > 1:
            seance = seances.filtered(lambda s: str(s.id) == str(choix or ""))
            if not seance:
                return {
                    "titre": _("Which session?"),
                    "message": _("Several sessions are taking place here "
                                 "right now."),
                    "choix": [{"cle": str(s.id), "libelle": "%s · %s" % (s.name, self._heure(s.date_begin)),
                               "style": "secondaire", "saisie": None} for s in seances],
                }
        else:
            seance = seances

        personne = self.env.user.partner_id
        Inscription = self.env["event.registration"].sudo()
        inscription = Inscription.search([
            ("event_id", "=", seance.id), ("partner_id", "=", personne.id),
            ("state", "!=", "cancel"),
        ], limit=1)
        if inscription.state == "done":
            return {"titre": seance.name, "url": None,
                    "message": _("Your attendance was already recorded.")}
        nouvelle = not inscription
        if nouvelle:
            inscription = Inscription.create({
                "event_id": seance.id, "partner_id": personne.id,
                "name": personne.name, "email": personne.email,
            })
        inscription.action_set_done()
        if params.get("differe"):
            inscription.message_post(
                body=_("Attendance recorded by tag at %s, sent later.",
                       self._heure(moment, "%Y-%m-%d %H:%M")),
                message_type="comment", subtype_xmlid="mail.mt_note")
        return {
            "titre": seance.name, "url": None,
            "message": _("Attendance recorded, and registration created: "
                         "you were not registered.")
            if nouvelle else _("Attendance recorded. Enjoy the session."),
        }

    def _seance_en_cours(self, seance, moment):
        return seance.date_begin - AVANT <= moment <= seance.date_end + APRES
