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
        selection_add=[("loan", "Prendre ou rendre un équipement")],
        ondelete={"loan": "cascade"},
    )

    def _executer_loan(self, tag, tap, params):
        self.ensure_one()
        self._exiger_une_personne(params)
        equipement = tag._cible()
        if not equipement or equipement._name != "bf.nfc.equipment":
            raise UserError(_("Cette pastille ne désigne pas un équipement."))
        moi = self.env.user
        moment = self._moment(params)
        differe = bool(params.get("differe"))
        sens = params.get("sens")
        detenteur = equipement.sudo().holder_id
        depuis = equipement.sudo().since

        if detenteur == moi:
            if sens == "prendre":
                return {"titre": equipement.name, "url": None,
                        "message": _("Vous l'avez déjà depuis le %s.", self._heure(depuis, "%Y-%m-%d %H:%M"))}
            equipement._rendre(moi, moment)
            return {"titre": equipement.name, "url": None,
                    "message": _("Rendu. Merci de le ranger : %s.", equipement.place)
                    if equipement.place else _("Rendu.")}

        if sens == "rendre":
            raise UserError(_("Vous n'avez pas « %s » : rien à rendre.", equipement.name))

        if detenteur and params.get("choix") != "reprendre":
            # La seule question du geste : l'équipement est entre d'autres mains.
            return {
                "titre": equipement.name,
                "message": _("%(qui)s l'a depuis le %(quand)s.", qui=detenteur.name,
                             quand=self._heure(depuis, "%Y-%m-%d %H:%M")),
                "choix": [{"cle": "reprendre", "libelle": _("Je le prends quand même"),
                           "style": "principal", "saisie": None}],
            }

        precedent = equipement._prendre(moi, moment, differe)
        if precedent:
            equipement.sudo().message_post(
                body=_("%(moi)s a repris l'équipement à %(autre)s.", moi=moi.name, autre=precedent.user_id.name),
                message_type="comment", subtype_xmlid="mail.mt_note")
            return {"titre": equipement.name, "url": None,
                    "message": _("Vous l'avez repris à %s.", precedent.user_id.name)}
        return {"titre": equipement.name, "url": None,
                "message": _("Il est à vous. Approchez de nouveau le téléphone pour le rendre.")}
