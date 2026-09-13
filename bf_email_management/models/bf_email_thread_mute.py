"""La sourdine : un fil qui continue sans nous.

Gmail a ``m`` depuis toujours. Nous n'avions rien, et le corpus disait ce que
ça coûte : mesuré sur une base réelle le 2026-09-13, **164 fils portent trois
messages reçus ou plus sans qu'on y ait jamais répondu**, 618 lignes en tout,
le plus gros à 40 messages (un fil de compilation d'images sur GitHub).

Une règle de tri sait déjà sortir un EXPÉDITEUR de la boîte. Elle ne sait pas
sortir un FIL, et c'est toute la différence : on veut suivre GitHub, pas ce
fil-là de GitHub.

⚠️ La sourdine ne jette rien et ne marque rien comme lu. Elle retire de la
boîte, comme « Traité », et le fil reste entier dans « Tous les courriels » et
dans son dossier. Un fil mis en sourdine dont un message nous nomme
explicitement resterait silencieux : c'est le comportement de Gmail, et c'est
assumé, parce que l'inverse rendrait la sourdine inutile sur les fils où l'on
est justement en copie.
"""
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class BfEmailThreadMute(models.Model):
    _name = "bf.email.thread.mute"
    _description = "Fil de courriels en sourdine"
    _order = "create_date desc, id desc"

    user_id = fields.Many2one(
        comodel_name="res.users",
        string="Propriétaire",
        required=True,
        index=True,
        ondelete="cascade",
        default=lambda self: self.env.user,
    )
    thread_root_id = fields.Char(
        string="Racine du fil",
        required=True,
        index=True,
        help="Le Message-ID de la racine RFC 2822, tel que `bf.email` le "
             "porte. C'est la seule clé qui tient quand l'objet change.",
    )
    subject = fields.Char(
        string="Objet au moment de la mise en sourdine",
        help="Une photo, pas un lien : l'objet d'un fil bouge, et une liste de "
             "fils en sourdine sans intitulé lisible ne se relit pas.",
    )

    _sql_constraints = [
        ("bf_email_thread_mute_uniq",
         "unique(user_id, thread_root_id)",
         "Ce fil est déjà en sourdine."),
    ]

    @api.model
    def _muted_roots(self, user=None):
        """Les racines que cet usager a mises en sourdine."""
        user = user or self.env.user
        rows = self.sudo().search_read(
            [("user_id", "=", user.id)], ["thread_root_id"])
        return [r["thread_root_id"] for r in rows if r["thread_root_id"]]

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._stamp_rows(True)
        return records

    def unlink(self):
        # Les lignes reprennent leur place AVANT que la fiche disparaisse :
        # après, on ne saurait plus quel fil réveiller.
        self._stamp_rows(False)
        return super().unlink()

    def _stamp_rows(self, muted):
        """Pose ou retire le drapeau sur toutes les lignes du fil.

        ⚠️ Le drapeau est sur la LIGNE et non calculé à la lecture, et ce n'est
        pas de la redondance : le badge du systray est écrit en JavaScript et
        ne peut pas interroger une table de sourdines ; le filtre du téléphone
        est du SQL à la main. Un booléen indexé est la seule forme que les
        trois surfaces savent lire pareil.
        """
        BfEmail = self.env["bf.email"].sudo()
        for mute in self:
            rows = BfEmail.search([
                ("user_id", "=", mute.user_id.id),
                ("thread_root_id", "=", mute.thread_root_id),
            ])
            if rows:
                rows.write({"is_muted": muted})

    def action_unmute(self):
        """Rend le fil à la boîte."""
        self.check_access("unlink")
        self.unlink()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Sourdine levée"),
                "message": _("Le fil revient dans la boîte de réception."),
                "type": "success",
                "sticky": False,
            },
        }
