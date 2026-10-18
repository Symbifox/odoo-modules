# -*- coding: utf-8 -*-
import logging

from odoo import SUPERUSER_ID, _, api, models

_logger = logging.getLogger(__name__)

# États pour lesquels le rendez-vous est considéré comme obtenu.
BOOKED_STATES = ("scheduled", "confirmed")


class ResourceBooking(models.Model):
    _inherit = "resource.booking"

    @api.model_create_multi
    def create(self, vals_list):
        bookings = super().create(vals_list)
        bookings._bf_outreach_sync()
        return bookings

    def write(self, vals):
        res = super().write(vals)
        # `state` est calculé : il bouge sur un changement de date, de réunion
        # ou de combinaison. On resynchronise donc largement plutôt que de
        # guetter une écriture directe sur `state`, qui n'arrive jamais.
        self._bf_outreach_sync()
        return res

    def _bf_outreach_summary(self):
        """Le titre de l'interaction : le nom de la réservation, sinon un libellé.

        Le libellé suit la langue du CONTEXTE de l'appel : l'appelant le règle sur
        celle de qui lira l'interaction, pas de qui a réservé (souvent un visiteur).
        """
        self.ensure_one()
        return self.name or _("Booking: %s", self.type_id.name or "")

    def _bf_outreach_reader_lang(self, target):
        """La langue de qui lira l'interaction : le responsable de la cible d'abord.

        ``user_id`` d'une réservation vaut par défaut l'usager qui la crée, souvent
        OdooBot ou le compte public d'un visiteur : il ne passe qu'en second, et
        jamais s'il s'agit du superutilisateur ou d'un compte partagé.
        """
        self.ensure_one()
        for user in (target.user_id, self.user_id):
            if user and user.active and not user.share and user.id != SUPERUSER_ID:
                return user.lang
        return self.env.company.partner_id.lang or self.env.lang

    def _bf_outreach_sync(self):
        """Journalise le rendez-vous sur la cible de démarchage correspondante.

        🔴 En sudo, et tout le rapprochement sous le ``try`` : qui crée ou
        déplace un rendez-vous n'a pas forcément les droits Démarchage. La
        recherche des cibles se faisait avec ses droits, hors du ``try`` : son
        AccessError refusait la réservation elle-même (18.0.1.1.0). Le journal
        est un effet de bord du système ; rien n'en remonte à l'usager.

        Le sudo lève la règle de société : le domaine la remet (la société du
        type, à défaut les sociétés actives de l'usager, comme la règle), sinon
        une réservation de A écrirait dans le démarchage de B. Chaque ``try`` a
        son point de sauvegarde : une erreur SQL avalée laisserait la
        transaction avortée, et la réservation tomberait plus loin, sans dire
        pourquoi.
        """
        if self.env.context.get("bf_outreach_no_sync"):
            return
        # Ce qui attend d'être écrit appartient à l'appelant : vidé ici, hors du
        # ``try``, son erreur remonte telle quelle au lieu d'être avalée par le
        # premier point de sauvegarde (qui vide tout en entrant).
        self.env.flush_all()
        Target = self.env["bf.outreach.target"].sudo()
        Touch = self.env["bf.outreach.touch"].sudo()
        meeting_stage = self.env.ref(
            "bf_outreach.stage_meeting", raise_if_not_found=False
        )
        for booking in self:
            if booking.state not in BOOKED_STATES or not booking.partner_id:
                continue
            company_ids = booking.type_id.company_id.ids or booking.env.companies.ids
            try:
                with self.env.cr.savepoint():
                    targets = Target.search(
                        [
                            ("partner_id", "=", booking.partner_id.id),
                            ("campaign_state", "in", ("draft", "running")),
                            ("stage_type", "not in", ("won", "lost")),
                            ("company_id", "in", [False, *company_ids]),
                        ]
                    )
            except Exception:  # noqa: BLE001
                _logger.exception(
                    "bf_outreach_appointment : recherche des cibles impossible pour "
                    "la réservation %s",
                    booking.id,
                )
                continue
            for target in targets:
                try:
                    with self.env.cr.savepoint():
                        if Touch.search_count(
                            [("target_id", "=", target.id), ("booking_id", "=", booking.id)]
                        ):
                            continue
                        Touch.create(
                            {
                                "target_id": target.id,
                                "kind": "meeting",
                                "direction": "in",
                                "outcome": "interested",
                                "date": booking.start or booking.create_date,
                                "duration": (booking.duration or 0.0) * 60.0,
                                "summary": booking.with_context(
                                    lang=booking._bf_outreach_reader_lang(target)
                                )._bf_outreach_summary(),
                                "booking_id": booking.id,
                                "user_id": (booking.user_id or target.user_id).id,
                            }
                        )
                        if meeting_stage and target.stage_type != "won":
                            target.stage_id = meeting_stage
                except Exception:  # noqa: BLE001
                    # Une commodité ne doit jamais faire échouer une réservation.
                    _logger.exception(
                        "bf_outreach_appointment : échec du rapprochement pour la "
                        "réservation %s",
                        booking.id,
                    )
