"""Le geste « je suis à la rencontre » : une ligne de présence.

⚠️ Écrit en sudo : les présences d'une rencontre sont réservées au groupe des
rencontres. Se noter présent n'est pas un privilège ; la pastille est la
permission, et le geste n'écrit que la présence de la personne qui tape.

⚠️ Une présence déjà « Absent » ou « Excusé » passe à « Présent » : la personne est
là, c'est le fait le plus récent. L'inverse n'arrive jamais par une pastille.
"""
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

# 🔴 Les aides portent le nom du satellite (`_rencontre_…`). Elles s'ajoutent au
# MÊME modèle `bf.nfc.gesture` que celles des autres satellites : un `_en_cours`
# ici écrasait celui de `bf_nfc_event` selon l'ordre de chargement, et la présence
# à une séance appelait la version des rencontres.
AVANT = timedelta(minutes=30)
APRES = timedelta(minutes=15)


class BfNfcGesture(models.Model):
    _inherit = "bf.nfc.gesture"

    kind = fields.Selection(
        selection_add=[("meeting_presence", "Noter ma présence à la rencontre")],
        ondelete={"meeting_presence": "cascade"},
    )

    @api.model
    def _modeles_supplementaires(self):
        return super()._modeles_supplementaires() + ["meeting.record"]

    def _executer_meeting_presence(self, tag, tap, params):
        self.ensure_one()
        self._exiger_une_personne(params)
        moment = self._moment(params)
        moi = self.env.user
        Rencontre = self.env["meeting.record"].sudo()
        # ⚠️ En sudo : une rencontre n'est lisible que par le groupe des rencontres,
        # et la pastille qui la vise est déjà la permission de s'y dire présent.
        cible = tag._cible(superutilisateur=True) if tag.res_model and tag.res_id else None
        if cible and cible._name == "meeting.record":
            rencontres = Rencontre.browse(cible.id)
            if not self._rencontre_en_cours(rencontres, moment):
                raise UserError(_("« %(nom)s » a lieu le %(quand)s : la présence se note pendant "
                                  "la rencontre.", nom=rencontres.name,
                                  quand=self._heure(rencontres.date, "%Y-%m-%d %H:%M")))
        elif cible:
            raise UserError(_("Cette pastille doit viser une rencontre, ou aucune fiche."))
        else:
            proches = Rencontre.search([
                ("date", "<=", moment + AVANT),
                ("date", ">=", moment - timedelta(hours=12)),
            ])
            rencontres = proches.filtered(
                lambda r: self._rencontre_en_cours(r, moment) and self._rencontre_invite(r, moi))
            if not rencontres:
                raise UserError(_("Aucune rencontre en cours à laquelle vous êtes invité."))

        choix = params.get("choix")
        if len(rencontres) > 1:
            rencontre = rencontres.filtered(lambda r: str(r.id) == str(choix or ""))
            if not rencontre:
                return {
                    "titre": _("Quelle rencontre ?"),
                    "message": _("Vous êtes invité à plusieurs rencontres en ce moment."),
                    "choix": [{"cle": str(r.id), "libelle": "%s · %s" % (r.name, self._heure(r.date)),
                               "style": "secondaire", "saisie": None} for r in rencontres],
                }
        else:
            rencontre = rencontres

        Presence = self.env["meeting.attendance"].sudo()
        presence = Presence.search([("meeting_id", "=", rencontre.id),
                                    ("partner_id", "=", moi.partner_id.id)], limit=1)
        if presence.status == "present":
            return {"titre": rencontre.name, "url": None, "message": _("Votre présence était déjà notée.")}
        if presence:
            presence.status = "present"
        else:
            Presence.create({"meeting_id": rencontre.id, "partner_id": moi.partner_id.id, "status": "present"})
        if params.get("differe"):
            rencontre.message_post(
                body=_("%(qui)s : présence notée par pastille à %(quand)s, envoyée en différé.",
                       qui=moi.name, quand=self._heure(moment, "%Y-%m-%d %H:%M")),
                message_type="comment", subtype_xmlid="mail.mt_note")
        return {"titre": rencontre.name, "url": None, "message": _("Présence notée. Bonne rencontre.")}

    def _rencontre_en_cours(self, rencontre, moment):
        duree = timedelta(minutes=rencontre.duration_minutes or 60)
        return rencontre.date - AVANT <= moment <= rencontre.date + duree + APRES

    def _rencontre_invite(self, rencontre, personne):
        partenaire = personne.partner_id
        return (partenaire in rencontre.invited_ids
                or partenaire in rencontre.calendar_event_id.partner_ids
                or rencontre.organizer_id == personne
                or partenaire in rencontre.attendance_ids.partner_id)
