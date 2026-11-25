"""Le geste du prêt : prendre, rendre, reprendre.

⚠️ Sans bouton dans les deux cas courants. Libre, on le prend ; à soi, on le
rend. Une question seulement quand quelqu'un d'autre l'a : c'est le seul cas où
la personne doit décider quelque chose.

⚠️ Les écritures se font en sudo APRÈS la lecture de l'équipement avec les droits
de qui tape : prendre un portable n'est pas un privilège de gestion, et la
pastille est la permission. C'est aussi ce qui permet le différé.
"""
from odoo import _, fields, models
from odoo.exceptions import UserError


class BfNfcGesture(models.Model):
    _inherit = "bf.nfc.gesture"

    kind = fields.Selection(
        selection_add=[("loan", "Take or return equipment")],
        ondelete={"loan": "cascade"},
    )

    def _executer_loan(self, tag, tap, params):
        self.ensure_one()
        self._exiger_une_personne(params)
        equipement = tag._cible()
        if not equipement or equipement._name != "bf.nfc.equipment":
            raise UserError(_("This tag does not point to a piece of "
                              "equipment."))
        moi = self.env.user
        moment = self._moment(params)
        differe = bool(params.get("differe"))
        if equipement.sudo().registre_externe:
            return self._pret_externe(equipement.sudo(), moi, moment, differe, params)
        sens = params.get("sens")
        detenteur = equipement.sudo().holder_id
        depuis = equipement.sudo().since

        if detenteur == moi:
            if sens == "prendre":
                return {"titre": equipement.name, "url": None,
                        "message": _("You have had it since %s already.", self._heure(depuis, "%Y-%m-%d %H:%M"))}
            equipement._rendre(moi, moment)
            return {"titre": equipement.name, "url": None,
                    "message": _("Returned. Please put it back: %s.", equipement.place)
                    if equipement.place else _("Returned.")}

        if sens == "rendre":
            raise UserError(_("You don't have “%s”: nothing to return.", equipement.name))

        if detenteur and params.get("choix") != "reprendre":
            # La seule question du geste : l'équipement est entre d'autres mains.
            return {
                "titre": equipement.name,
                "message": _("%(qui)s has had it since %(quand)s.", qui=detenteur.name,
                             quand=self._heure(depuis, "%Y-%m-%d %H:%M")),
                "choix": [{"cle": "reprendre", "libelle": _("I'll take it "
                                                            "anyway"),
                           "style": "principal", "saisie": None}],
            }

        precedent = equipement._prendre(moi, moment, differe)
        if precedent:
            equipement.sudo().message_post(
                body=_("%(moi)s took the equipment back from %(autre)s.", moi=moi.name, autre=precedent.user_id.name),
                message_type="comment", subtype_xmlid="mail.mt_note")
            return {"titre": equipement.name, "url": None,
                    "message": _("You took it back from %s.", precedent.user_id.name)}
        return {"titre": equipement.name, "url": None,
                "message": _("It's yours. Bring the phone close again to "
                             "return it.")}

    def _pret_externe(self, equipement, moi, moment, differe, params):
        """Le registre des cadenas : on remet à quelqu'un qui se nomme, on reprend d'un tapotement.

        ⚠️ Deux questions, jamais d'écriture sans réponse : remettre demande l'identité
        de qui reçoit, reprendre demande de confirmer. Un cadenas qu'on croit repris et
        qui est encore posé sur une machine est exactement l'accident que ce registre
        existe pour éviter.
        """
        pret = equipement.current_loan_id
        choix = params.get("choix")
        if pret:
            if choix != "reprendre":
                return {
                    "titre": equipement.name,
                    "message": _("Handed out to %(qui)s on %(quand)s.", qui=pret.holder_label,
                                 quand=self._heure(pret.date_start, "%Y-%m-%d %H:%M")),
                    "choix": [{"cle": "reprendre", "libelle": _("It has "
                                                                "been "
                                                                "returned"),
                               "style": "principal", "saisie": None}],
                }
            equipement._rendre(moi, moment)
            return {"titre": equipement.name, "url": None,
                    "message": _("Return recorded in the register: %s.", pret.holder_label)}
        formulaire = [
            {"cle": "nom", "libelle": _("Handed out to (name)"), "type": "texte", "requis": True,
             "unite": None, "min": None, "max": None, "options": None, "aide": None},
            {"cle": "telephone", "libelle": _("Phone"), "type": "texte", "requis": True,
             "unite": None, "min": None, "max": None, "options": None, "aide": None},
            {"cle": "employeur", "libelle": _("Employer"), "type": "texte", "requis": False,
             "unite": None, "min": None, "max": None, "options": None,
             "aide": _("If they work for another company.")},
        ]
        if choix != "remettre":
            return {
                "titre": equipement.name,
                "message": _("Who are you handing it to?"),
                "formulaire": formulaire,
                "choix": [{"cle": "remettre", "libelle": _("Hand out"), "style": "principal", "saisie": None}],
            }
        lues = self._lire_formulaire(formulaire, params)
        equipement._prendre(moi, moment, differe, externe=lues)
        return {"titre": equipement.name, "url": None,
                "message": _("Handed out to %s: recorded in the register.", lues["nom"])}
