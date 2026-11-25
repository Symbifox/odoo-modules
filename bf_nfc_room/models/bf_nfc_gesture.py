"""Le geste de la porte : lire l'état de la salle, puis agir sur un choix.

⚠️ Sans choix, le geste ne fait que DIRE (libre jusqu'à, occupée par) et proposer.
Il n'agit jamais sur un premier tapotement : prendre une salle par erreur en
passant devant la porte bloquerait la salle d'un collègue.

⚠️ L'état se relit AU MOMENT du choix. Entre la question et le bouton, quelqu'un
peut avoir pris la salle par l'agenda ; la contrainte de chevauchement tranche,
et le refus le dit.
"""
from datetime import timedelta

from odoo import _, fields, models
from odoo.exceptions import UserError


class BfNfcGesture(models.Model):
    _inherit = "bf.nfc.gesture"

    kind = fields.Selection(
        selection_add=[("room", "Meeting room")],
        ondelete={"room": "cascade"},
    )

    def _executer_room(self, tag, tap, params):
        self.ensure_one()
        self._exiger_une_personne(params)
        salle = tag._cible()
        if not salle or salle._name != "bf.nfc.room":
            raise UserError(_("This tag does not point to a room."))
        maintenant = self._moment(params)
        moi = self.env.user
        Evenement = self.env["calendar.event"].sudo()
        delai = timedelta(minutes=salle.checkin_delay or 10)

        def a_moi(evenement):
            return evenement.user_id == moi or moi.partner_id in evenement.partner_ids

        def fin_de(evenement):
            return self._heure(evenement.stop)

        en_cours = Evenement.search([
            ("bf_nfc_room_id", "=", salle.id), ("start", "<=", maintenant), ("stop", ">", maintenant),
        ], order="start", limit=1)
        prochain = Evenement.search([
            ("bf_nfc_room_id", "=", salle.id), ("start", ">", maintenant),
        ], order="start", limit=1)

        action, _sep, valeur = (params.get("choix") or "").partition(":")
        if action == "arrive":
            evenement = Evenement.browse(int(valeur or 0)).exists()
            if not evenement or evenement.bf_nfc_room_id != salle or not a_moi(evenement) \
                    or not (evenement.start - delai <= maintenant < evenement.stop):
                raise UserError(_("This booking no longer needs confirming."))
            evenement.bf_nfc_checkin = maintenant
            return {"titre": salle.name, "url": None,
                    "message": _("Confirmed: the room is yours until %s.", fin_de(evenement))}
        if action == "liberer":
            evenement = Evenement.browse(int(valeur or 0)).exists()
            if not evenement or evenement.bf_nfc_room_id != salle or not a_moi(evenement):
                raise UserError(_("This booking is not yours."))
            if evenement.start >= maintenant:
                evenement.bf_nfc_room_id = False
            else:
                evenement.stop = maintenant
            return {"titre": salle.name, "url": None, "message": _("Room "
                                                                   "released. "
                                                                   "Thank "
                                                                   "you.")}
        if action == "prendre":
            if en_cours:
                raise UserError(_("The room was just taken, until %s.", fin_de(en_cours)))
            fin = maintenant + timedelta(minutes=int(valeur or 0))
            if prochain and prochain.start < fin:
                fin = prochain.start
            if fin - maintenant < timedelta(minutes=5):
                raise UserError(_("The room is booked at %s: too little "
                                  "time to take it.",
                                  self._heure(prochain.start)))
            self.env["calendar.event"].create({
                "name": _("%(salle)s · %(qui)s", salle=salle.name, qui=moi.name),
                "start": maintenant, "stop": fin,
                "user_id": moi.id, "partner_ids": [(6, 0, [moi.partner_id.id])],
                "bf_nfc_room_id": salle.id, "bf_nfc_checkin": maintenant,
            })
            return {"titre": salle.name, "url": None,
                    "message": _("Room taken until %s. Release it when "
                                 "you leave.", self._heure(fin))}
        if action:
            raise UserError(_("This choice does not exist."))

        # ── Sans choix : dire, et proposer ───────────────────────────────
        if en_cours:
            if a_moi(en_cours):
                choix = []
                if not en_cours.bf_nfc_checkin:
                    choix.append({"cle": "arrive:%s" % en_cours.id, "libelle": _("I'm "
                                                                                 "here"),
                                  "style": "principal", "saisie": None})
                choix.append({"cle": "liberer:%s" % en_cours.id, "libelle": _("Release "
                                                                              "the "
                                                                              "room"),
                              "style": "danger", "saisie": None})
                return {"titre": salle.name, "choix": choix,
                        "message": _("Your booking, until %s.", fin_de(en_cours))}
            qui = en_cours.user_id.name or _("someone")
            return {"titre": salle.name, "choix": [],
                    "message": _("Taken by %(qui)s until %(fin)s.", qui=qui, fin=fin_de(en_cours))}

        choix = []
        if prochain and a_moi(prochain) and prochain.start - delai <= maintenant:
            choix.append({"cle": "arrive:%s" % prochain.id, "style": "principal", "saisie": None,
                          "libelle": _("I'm here (booking at %s)", self._heure(prochain.start))})
        for minutes in salle._durees():
            if not prochain or maintenant + timedelta(minutes=minutes) <= prochain.start:
                libelle = _("Take %s min", minutes) if minutes < 60 or minutes % 60 else \
                    _("Take %s h", minutes // 60)
                choix.append({"cle": "prendre:%s" % minutes, "libelle": libelle,
                              "style": "secondaire", "saisie": None})
        if prochain and not any(c["cle"].startswith("prendre") for c in choix):
            reste = int((prochain.start - maintenant).total_seconds() // 60)
            if reste >= 10:
                choix.append({"cle": "prendre:%s" % reste, "style": "secondaire", "saisie": None,
                              "libelle": _("Take until %s", self._heure(prochain.start))})
        message = _("Free until %s.", self._heure(prochain.start)) if prochain \
            else _("Free for the rest of the day.")
        return {"titre": salle.name, "message": message, "choix": choix}
