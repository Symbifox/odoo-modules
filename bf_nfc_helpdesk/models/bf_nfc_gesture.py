"""Le geste « signaler un problème » : un billet déjà rempli.

⚠️ Le billet est créé en sudo, APRÈS la lecture de la fiche avec les droits de qui
tape. Signaler une panne n'est pas un privilège de l'équipe de soutien : la
pastille est la permission, et c'est ce qui laisse un client sans compte
signaler par une pastille signée.

⚠️ `sudo()` garde l'utilisateur : la personne qui signale devient abonnée du billet,
peut le lire et en reçoit les suites. L'adresse n'est rendue qu'à qui peut le lire.

⚠️ Le billet prend l'étape d'entrée de l'équipe. Si cette étape porte un modèle
de courriel, `helpdesk_mgmt` l'envoie au client : c'est son réglage, pas le nôtre.
"""
from markupsafe import Markup, escape

from odoo import _, fields, models
from odoo.exceptions import UserError


class BfNfcGesture(models.Model):
    _inherit = "bf.nfc.gesture"

    kind = fields.Selection(
        selection_add=[("ticket", "Signaler un problème")],
        ondelete={"ticket": "cascade"},
    )

    def _executer_ticket(self, tag, tap, params):
        self.ensure_one()
        cible = tag._cible()
        if not cible:
            raise UserError(_("Cette pastille ne désigne aucune fiche."))
        client = cible if cible._name == "res.partner" else (
            cible.partner_id if "partner_id" in cible._fields else cible.env["res.partner"])
        texte = (params.get("texte_fixe") or params.get("texte") or "").strip()
        if not texte:
            if params.get("choix") == "signaler":
                raise UserError(_("Décrivez le problème en une phrase avant d'envoyer."))
            return {
                "titre": _("Signaler un problème"),
                "message": " · ".join(filter(None, [cible.display_name, tag.place])),
                "choix": [{"cle": "signaler", "libelle": _("Décrire le problème"),
                           "style": "principal", "saisie": "texte"}],
            }

        moment = self._moment(params)
        qui = self.env.user
        lignes = [escape(texte)]
        details = [
            _("Fiche : %s", cible.display_name),
            _("Endroit : %s", tag.place) if tag.place else None,
            _("Signalé par %(qui)s le %(quand)s, par la pastille « %(pastille)s ».",
              qui=qui.name, quand=self._heure(moment, "%Y-%m-%d %H:%M"), pastille=tag.name),
            _("Envoyé en différé : le téléphone n'avait pas de réseau.") if params.get("differe") else None,
        ]
        corps = Markup("<p>%s</p><p><small>%s</small></p>") % (
            lignes[0], Markup("<br/>").join(escape(d) for d in details if d))
        valeurs = {
            "name": _("%(quoi)s : %(texte)s", quoi=tag.place or cible.display_name, texte=texte)[:200],
            "description": corps,
            "partner_id": client.id or False,
            "channel_id": self.env.ref("bf_nfc_helpdesk.channel_pastille").id,
        }
        equipe = params.get("equipe")
        if equipe:
            valeurs["team_id"] = int(equipe)
        billet = self.env["helpdesk.ticket"].sudo().create(valeurs)
        lisible = billet.with_user(qui).sudo(False).has_access("read")
        return {
            "titre": billet.number,
            "message": _("Billet %s créé. Merci, c'est signalé.", billet.number),
            "url": "/mail/view?model=helpdesk.ticket&res_id=%s" % billet.id if lisible else None,
        }
