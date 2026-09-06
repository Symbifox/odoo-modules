"""Le cron des rappels (2.53.0) : isolation par réservation, fenêtre bornée.

Deux propriétés, toutes deux tirées d'une panne réelle en production les
2026-08-30 et 2026-08-31 :

1. **Une réservation fautive ne doit pas emporter la run.** Là-bas, l'écriture
   de `sent_schedule_ids` sur UNE réservation faisait lever `_check_scheduling`
   d'OCA, et l'exception remontait hors de la boucle : plus aucun rappel n'est
   parti, pour aucune réservation, pendant 24 h.

2. **Un rappel « X heures avant » ne doit pas partir n'importe quand.** Sans
   borne haute, réserver à moins de X heures de l'échéance déclenchait le
   rappel dans la minute, et une reprise après panne rejouait un « rappel :
   demain » à deux heures de la rencontre.
"""

from datetime import timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("bf_appointment", "bf_appointment_cron")
class TestCronRappels(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True, tz="UTC"))
        attendances = [
            Command.create({
                "name": f"All day {d}", "dayofweek": str(d),
                "hour_from": 0.0, "hour_to": 24.0, "day_period": "morning",
            })
            for d in range(7)
        ]
        cls.calendar = cls.env["resource.calendar"].create({
            "name": "24/7 Rappels", "attendance_ids": attendances, "tz": "UTC",
        })
        # Deux ressources : deux réservations peuvent alors coexister à la même
        # heure sans buter sur « toutes les ressources sont occupées ».
        cls.combinations = cls.env["resource.booking.combination"]
        for indice in range(2):
            ressource = cls.env["resource.resource"].create({
                "name": f"Rappels material {indice}",
                "calendar_id": cls.calendar.id,
                "resource_type": "material", "tz": "UTC",
            })
            cls.combinations |= cls.env["resource.booking.combination"].create({
                "resource_ids": [Command.set([ressource.id])],
            })
        cls.partner = cls.env["res.partner"].create({
            "name": "Rappels Booker", "email": "rappels@test.invalid",
        })
        cls.booking_type = cls.env["resource.booking.type"].create({
            "name": "Rappels Type", "duration": 1.0, "slot_duration": 1.0,
            "modifications_deadline": 0.0, "combination_assignment": "sorted",
            "resource_calendar_id": cls.calendar.id,
            "combination_rel_ids": [
                Command.create({"sequence": indice, "combination_id": combinaison.id})
                for indice, combinaison in enumerate(cls.combinations)
            ],
        })
        cls.template = cls.env["mail.template"].create({
            "name": "Rappel test", "subject": "Rappel",
            "model_id": cls.env["ir.model"]._get_id("resource.booking"),
            "body_html": "<p>Rappel</p>",
        })

    def _planification(self, heures):
        return self.env["appointment.email.schedule"].create({
            "type_id": self.booking_type.id, "trigger": "before",
            "hours": heures, "template_id": self.template.id,
        })

    def _reservation(self, dans, combinaison=None):
        """Une réservation confirmée dont le début est à `dans` de maintenant."""
        booking = self.env["resource.booking"].create({
            "partner_ids": [(6, 0, [self.partner.id])],
            "type_id": self.booking_type.id,
            "combination_id": (combinaison or self.combinations[0]).id,
            "combination_auto_assign": False,
            "start": fields.Datetime.now() + dans,
            "duration": 1.0,
        })
        booking.state = "confirmed"
        return booking

    def _planification_sms(self, heures):
        """Une planification par SMS — le seul chemin du cron dont l'échec
        remonte pour de vrai.

        `_send_appointment_sms` est sous `try/except`, mais
        `_appointment_sms_phone()` juste après ne l'est pas : c'est là qu'on
        injecte la panne, APRÈS que la planification a été revendiquée. C'est
        exactement la forme de l'incident, où c'est l'écriture de
        `sent_schedule_ids` elle-même qui levait.
        """
        return self.env["appointment.email.schedule"].create({
            "type_id": self.booking_type.id, "trigger": "before",
            "hours": heures, "template_id": self.template.id,
            "channel": "sms", "sms_body": "Rappel: rendez-vous bientot.",
        })

    # -- 1. isolation --------------------------------------------------------

    def test_une_reservation_fautive_n_emporte_pas_les_autres(self):
        """La panne : une seule réservation levait, tout le monde perdait.

        Sans l'isolation, l'exception remonte hors de la boucle et l'appel au
        cron lui-même échoue — la réservation saine ne reçoit rien.
        """
        self._planification_sms(1.0)
        fautive = self._reservation(timedelta(minutes=50), self.combinations[0])
        saine = self._reservation(timedelta(minutes=50), self.combinations[1])

        def _telephone(self):
            if self.id == fautive.id:
                raise ValidationError(
                    "Cannot schedule these bookings because no resources "
                    "are selected for them"
                )
            return False

        with patch.object(
            type(fautive), "_send_appointment_sms", autospec=True, return_value=False
        ), patch.object(
            type(fautive), "_appointment_sms_phone", autospec=True, side_effect=_telephone
        ), patch.object(
            type(fautive), "_send_appointment_email", autospec=True
        ) as envoi:
            self.env["resource.booking"]._cron_send_appointment_emails()

        destinataires = {appel.args[0].id for appel in envoi.call_args_list}
        self.assertIn(
            saine.id, destinataires,
            "La réservation saine doit recevoir son rappel malgré la fautive",
        )
        self.assertNotIn(fautive.id, destinataires)

    def test_l_echec_annule_la_revendication_au_lieu_de_la_garder(self):
        """Le point de reprise doit ANNULER la revendication faite avant l'échec.

        La planification est revendiquée (`sent_schedule_ids`) AVANT l'envoi.
        Si le retour en arrière ne l'emportait pas, la réservation resterait
        marquée « rappel envoyé » alors que rien n'est parti : le rappel serait
        perdu définitivement, au prochain tick comme aux suivants.
        """
        planification = self._planification_sms(1.0)
        booking = self._reservation(timedelta(minutes=50))

        with patch.object(
            type(booking), "_send_appointment_sms", autospec=True, return_value=False
        ), patch.object(
            type(booking), "_appointment_sms_phone", autospec=True,
            side_effect=ValidationError("panne après revendication"),
        ):
            # Que cet appel RENDE la main est déjà la moitié du contrôle :
            # sans l'isolation, il lèverait.
            self.env["resource.booking"]._cron_send_appointment_emails()

        booking.invalidate_recordset(["sent_schedule_ids"])
        self.assertNotIn(
            planification, booking.sent_schedule_ids,
            "Une réservation avortée ne doit garder aucune revendication",
        )

    # -- 2. fenêtre bornée ---------------------------------------------------

    def test_reserver_dans_la_fenetre_ne_declenche_pas_le_rappel_veille(self):
        """Le cas Annie Zizka : réserver à 22 h de l'échéance.

        Le rappel « 24 h avant » ne doit PAS partir dans la minute qui suit.
        """
        planification = self._planification(24.0)
        booking = self._reservation(timedelta(hours=22))
        with patch.object(
            type(booking), "_send_appointment_email", autospec=True
        ) as envoi:
            self.env["resource.booking"]._cron_send_appointment_emails()
        self.assertEqual(
            envoi.call_count, 0,
            "Un rappel « 24 h avant » n'a pas de sens à 22 h de l'échéance",
        )
        self.assertNotIn(planification, booking.sent_schedule_ids)

    def test_le_rappel_part_normalement_dans_sa_fenetre(self):
        """Le contrôle doit DISCRIMINER : à l'heure dite, le rappel part."""
        planification = self._planification(24.0)
        booking = self._reservation(timedelta(hours=23, minutes=45))
        with patch.object(
            type(booking), "_send_appointment_email", autospec=True
        ) as envoi:
            self.env["resource.booking"]._cron_send_appointment_emails()
        self.assertEqual(envoi.call_count, 1)
        self.assertIn(planification, booking.sent_schedule_ids)

    def test_un_rappel_court_garde_toute_sa_fenetre(self):
        """La borne ne doit pas rogner un préavis plus court qu'elle.

        La tolérance est plafonnée au préavis lui-même : pour un rappel « 30 min
        avant », la borne haute retombe sur le début de la rencontre, donc c'est
        le plafond `now < start` qui tranche, comme avant 2.53.0. Sans ce
        plafonnement, une tolérance fixe d'une heure resterait sans effet ici —
        mais un réglage plus large la rendrait incohérente.
        """
        planification = self._planification(0.5)
        booking = self._reservation(timedelta(minutes=5))
        with patch.object(
            type(booking), "_send_appointment_email", autospec=True
        ) as envoi:
            self.env["resource.booking"]._cron_send_appointment_emails()
        self.assertEqual(
            envoi.call_count, 1,
            "Dans son propre préavis, un rappel court doit encore partir",
        )
        self.assertIn(planification, booking.sent_schedule_ids)

    def test_un_rattrapage_tardif_apres_panne_est_supprime(self):
        """Reprise après interruption : pas de « rappel : demain » à 2 h du but."""
        planification = self._planification(24.0)
        booking = self._reservation(timedelta(hours=2))
        with patch.object(
            type(booking), "_send_appointment_email", autospec=True
        ) as envoi:
            self.env["resource.booking"]._cron_send_appointment_emails()
        self.assertEqual(envoi.call_count, 0)
        self.assertNotIn(planification, booking.sent_schedule_ids)

    def test_la_tolerance_est_reglable(self):
        """Un locataire doit pouvoir rouvrir la fenêtre sans toucher au code."""
        planification = self._planification(24.0)
        booking = self._reservation(timedelta(hours=2))
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_appointment.reminder_grace_hours", "48")
        with patch.object(
            type(booking), "_send_appointment_email", autospec=True
        ) as envoi:
            self.env["resource.booking"]._cron_send_appointment_emails()
        self.assertEqual(envoi.call_count, 1)
        self.assertIn(planification, booking.sent_schedule_ids)

    def test_un_reglage_illisible_retombe_sur_le_defaut(self):
        """Une valeur vide ou absurde ne doit pas faire planter le cron."""
        Booking = self.env["resource.booking"]
        for brut in ("", "beaucoup", "-3"):
            self.env["ir.config_parameter"].sudo().set_param(
                "bf_appointment.reminder_grace_hours", brut)
            self.assertEqual(
                Booking._cron_reminder_grace_hours(),
                Booking._CRON_REMINDER_GRACE_HOURS,
                f"« {brut} » doit retomber sur le défaut",
            )
