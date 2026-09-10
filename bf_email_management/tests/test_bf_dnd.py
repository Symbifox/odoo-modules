"""Mode « ne pas déranger ».

Ce que ces tests éprouvent, dans l'ordre où ça peut casser :

1. L'interrupteur d'instance ABSENT. C'est l'état de toute installation neuve,
   donc celui de tout locataire au prochain ``-u``. Un mode qui s'armerait
   là ferait taire des gens qui n'ont rien demandé.
2. Ce qui compte pour une rencontre. « Occupé » seul vaut trois fois plus
   d'heures sur trente jours d'agenda réel, parce qu'il avale les blocages de
   créneau ; « occupé + plus d'un participant + hors journée entière » ne
   contient que de vraies rencontres. Les deux filtres écartés à la mesure
   (RSVP accepté, lien vidéo) ont chacun leur test, pour qu'un ajout
   « évident » les fasse échouer bruyamment.
3. Le fuseau des heures calmes. ``res.partner.tz`` suit souvent le lieu de
   RÉSIDENCE : sur un compte réglé à ``Pacific/Auckland``, une fenêtre de
   22 h à 8 h lue là vaut 6 h à 16 h en Amérique de l'Est, soit une journée
   de travail passée sous silence.
4. Que ce qui est tu soit NOTÉ. Un avis oublié plutôt que retenu ne se
   remarque jamais : le résumé de sortie serait simplement vide.
5. Le résumé, une seule fois. Un cron à la minute qui rendrait le même résumé
   à chaque battement est pire que pas de résumé du tout.
"""
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import MobileApiCase

ALARM_MGR = "odoo.addons.bf_email_management.models.calendar_alarm_manager"


@tagged("post_install", "-at_install")
class TestBfDnd(MobileApiCase):

    def setUp(self):
        super().setUp()
        self.Dnd = self.env["bf.dnd"]
        self.Held = self.env["bf.dnd.held"].sudo()
        self.param = self.env["ir.config_parameter"].sudo()
        self.param.set_param("bf_email.dnd_enabled", "1")
        self.param.set_param("bf_email.popup_enabled", "1")
        self.account.popup_mode = "transient"
        self.owner.sudo().write({
            "bf_dnd_meetings": False,
            "bf_dnd_manual_until": False,
            "bf_dnd_manual_off_until": False,
            "bf_dnd_quiet_enabled": False,
            "bf_dnd_last_state": False,
        })
        self._mark()

    # ------------------------------------------------------------------
    # Outils
    # ------------------------------------------------------------------
    def _mark(self):
        """Repère : ce qui précède ne nous regarde plus.

        ⚠️ Le ``precommit.run()`` est LOAD-BEARING. ``_sendone`` n'écrit rien
        tout de suite, il empile dans ``cr.precommit`` ; poser le repère sans
        vider la pile le pose AVANT des messages déjà décidés, qui
        ressortiraient ensuite comme s'ils venaient d'être envoyés. C'est ce
        qui a fait croire, une fois, que le résumé de sortie sortait deux
        fois.
        """
        self.env.cr.precommit.run()
        self._watermark = self.env["bus.bus"].sudo().search(
            [], order="id desc", limit=1).id or 0
    def _bus(self, channel_type):
        """Les charges utiles poussées depuis le repère, pour ce type.

        ⚠️ ``_sendone`` empile dans ``cr.precommit`` ; une transaction de test
        ne valide jamais. Sans ce ``run()`` la table reste vide et tous les
        tests passeraient au vert sans rien éprouver.
        """
        import json
        self.env.cr.precommit.run()
        rows = self.env["bus.bus"].sudo().search(
            [("id", ">", self._watermark)])
        out = []
        for row in rows:
            message = json.loads(row.message)
            if message.get("type") == channel_type:
                out.append(message["payload"])
        return out

    def _meeting(self, minutes_ago=5, minutes_left=55, attendees=2,
                 allday=False, show_as="busy", state="needsAction"):
        """Une rencontre EN COURS, réglable sur chacun des critères."""
        partners = self.owner.partner_id
        for index in range(attendees - 1):
            partners |= self.env["res.partner"].create({
                "name": "Participant %s" % index,
                "email": "participant%s@test.invalid" % index,
            })
        now = fields.Datetime.now()
        event = self.env["calendar.event"].sudo().create({
            "name": "Statutaire d'essai",
            "start": now - timedelta(minutes=minutes_ago),
            "stop": now + timedelta(minutes=minutes_left),
            "allday": allday,
            "show_as": show_as,
            "partner_ids": [(6, 0, partners.ids)],
        })
        event.attendee_ids.write({"state": state})
        return event

    # ------------------------------------------------------------------
    # 1. L'interrupteur d'instance
    # ------------------------------------------------------------------
    def test_cle_absente_vaut_non(self):
        """Clé ABSENTE, pas « 0 » : l'état de toute installation neuve."""
        self.param.search([("key", "=", "bf_email.dnd_enabled")]).unlink()
        self.owner.sudo().bf_dnd_meetings = True
        self._meeting()
        self.assertFalse(self.Dnd._instance_enabled())
        self.assertFalse(self.Dnd._active_for(self.owner))

    def test_cle_vide_vaut_non(self):
        self.param.set_param("bf_email.dnd_enabled", "")
        self.owner.sudo().bf_dnd_meetings = True
        self._meeting()
        self.assertFalse(self.Dnd._active_for(self.owner))

    # ------------------------------------------------------------------
    # 2. Ce qui fait une rencontre
    # ------------------------------------------------------------------
    def test_rencontre_arme(self):
        self.owner.sudo().bf_dnd_meetings = True
        event = self._meeting()
        state = self.Dnd._state_for(self.owner)
        self.assertTrue(state["active"])
        self.assertEqual(state["reason"], "meeting")
        self.assertEqual(state["event"], event)
        self.assertEqual(state["until"], event.stop)

    def test_occupe_seul_n_arme_pas(self):
        """Un blocage de créneau à un seul participant n'est pas une rencontre."""
        self.owner.sudo().bf_dnd_meetings = True
        self._meeting(attendees=1)
        self.assertFalse(self.Dnd._active_for(self.owner))

    def test_journee_entiere_n_arme_pas(self):
        """« Jo and Stephen back » aurait éteint les avis 24 h d'affilée."""
        self.owner.sudo().bf_dnd_meetings = True
        self._meeting(allday=True)
        self.assertFalse(self.Dnd._active_for(self.owner))

    def test_libre_n_arme_pas(self):
        self.owner.sudo().bf_dnd_meetings = True
        self._meeting(show_as="free")
        self.assertFalse(self.Dnd._active_for(self.owner))

    def test_rsvp_non_repondu_arme_quand_meme(self):
        """⚠️ Filtre écarté à la mesure : 57 des 64 lignes sont à needsAction.

        Les événements venus de Nextcloud n'apportent aucun RSVP. Exiger
        « accepté » ramènerait le mode à 4 rencontres sur 29. Si quelqu'un
        ajoute ce filtre un jour, c'est ici que ça doit casser.
        """
        self.owner.sudo().bf_dnd_meetings = True
        self._meeting(state="needsAction")
        self.assertTrue(self.Dnd._active_for(self.owner))

    def test_sans_lien_video_arme_quand_meme(self):
        """⚠️ Second filtre écarté : présent sur 12 des 29 rencontres."""
        self.owner.sudo().bf_dnd_meetings = True
        event = self._meeting()
        self.assertFalse(event.videocall_location)
        self.assertTrue(self.Dnd._active_for(self.owner))

    def test_reglage_eteint_n_arme_pas(self):
        self.owner.sudo().bf_dnd_meetings = False
        self._meeting()
        self.assertFalse(self.Dnd._active_for(self.owner))

    def test_deux_rencontres_qui_se_chevauchent_tiennent_jusqu_a_la_derniere(self):
        self.owner.sudo().bf_dnd_meetings = True
        self._meeting(minutes_left=10)
        tardive = self._meeting(minutes_left=90)
        state = self.Dnd._state_for(self.owner)
        self.assertEqual(state["until"], tardive.stop)

    # ------------------------------------------------------------------
    # 3. L'interrupteur manuel, dans les deux sens
    # ------------------------------------------------------------------
    def test_manuel_arme_et_expire(self):
        now = fields.Datetime.now()
        self.owner.sudo().bf_dnd_manual_until = now + timedelta(minutes=5)
        self.assertTrue(self.Dnd._active_for(self.owner))
        self.owner.sudo().bf_dnd_manual_until = now - timedelta(minutes=1)
        self.assertFalse(self.Dnd._active_for(self.owner))

    def test_derange_moi_quand_meme_prime_sur_la_rencontre(self):
        self.owner.sudo().bf_dnd_meetings = True
        self._meeting()
        self.assertTrue(self.Dnd._active_for(self.owner))
        self.owner.sudo().bf_dnd_manual_off_until = (
            fields.Datetime.now() + timedelta(hours=1))
        self.assertFalse(self.Dnd._active_for(self.owner))

    def test_bouton_eteindre_pose_le_contre_interrupteur(self):
        """Éteindre pendant une rencontre doit TENIR.

        Sans le « me déranger quand même », le mode se rallumerait à la
        minute suivante et le geste passerait pour un défaut.
        """
        self.owner.sudo().bf_dnd_meetings = True
        self._meeting()
        self.owner.with_user(self.owner).action_bf_dnd_off()
        self.assertTrue(self.owner.sudo().bf_dnd_manual_off_until)
        self.assertFalse(self.Dnd._active_for(self.owner))

    def test_bouton_duree_lit_le_contexte(self):
        """⚠️ Un `context="{'minutes': 60}"` n'est PAS un argument nommé."""
        user = self.owner.with_user(self.owner).with_context(minutes=60)
        user.action_bf_dnd_for_minutes()
        delta = self.owner.sudo().bf_dnd_manual_until - fields.Datetime.now()
        self.assertGreater(delta, timedelta(minutes=55))

    def test_bouton_jusqu_a_la_fin_de_la_rencontre(self):
        self.owner.sudo().bf_dnd_meetings = True
        event = self._meeting(minutes_left=42)
        self.owner.with_user(self.owner).action_bf_dnd_until_meeting_end()
        self.assertEqual(self.owner.sudo().bf_dnd_manual_until, event.stop)

    # ------------------------------------------------------------------
    # 4. Les heures calmes et le fuseau
    # ------------------------------------------------------------------
    def _quiet(self, tz, start, end):
        self.owner.sudo().write({
            "bf_dnd_quiet_enabled": True,
            "bf_dnd_quiet_tz": tz,
            "bf_dnd_quiet_start": start,
            "bf_dnd_quiet_end": end,
        })

    def test_fenetre_simple(self):
        # 2026-09-09 18:00 UTC = 14:00 à Montréal.
        moment = fields.Datetime.to_datetime("2026-09-09 18:00:00")
        self._quiet("America/Toronto", 13.0, 15.0)
        self.assertTrue(self.Dnd._active_for(self.owner, now=moment))
        self._quiet("America/Toronto", 15.0, 17.0)
        self.assertFalse(self.Dnd._active_for(self.owner, now=moment))

    def test_fenetre_qui_passe_minuit(self):
        # 2026-09-10 04:00 UTC = 2026-09-10 00:00 à Montréal.
        moment = fields.Datetime.to_datetime("2026-09-10 04:00:00")
        self._quiet("America/Toronto", 22.0, 8.0)
        self.assertTrue(self.Dnd._active_for(self.owner, now=moment))
        # 2026-09-09 18:00 UTC = 14:00, hors fenêtre.
        self.assertFalse(self.Dnd._active_for(
            self.owner, now=fields.Datetime.to_datetime("2026-09-09 18:00:00")))

    def test_le_fuseau_est_celui_du_reglage_pas_celui_de_la_fiche(self):
        """🔴 Le piège mesuré sur un compte réel.

        Une fiche contact réglée à ``Pacific/Auckland`` fait valoir une fenêtre
        de 22 h à 8 h de 6 h à 16 h en Amérique de l'Est, soit une journée de
        travail entière. Le réglage a donc son propre fuseau, et c'est lui qui
        décide.
        """
        self.owner.sudo().tz = "Pacific/Auckland"
        # 2026-09-09 18:00 UTC = 14:00 à Montréal, et 2026-09-10 06:00 à
        # Auckland (NZST, UTC+12).
        moment = fields.Datetime.to_datetime("2026-09-09 18:00:00")
        self._quiet("America/Toronto", 22.0, 8.0)
        self.assertFalse(
            self.Dnd._active_for(self.owner, now=moment),
            "14 h à Montréal n'est pas dans une fenêtre de 22 h à 8 h",
        )
        self._quiet("Pacific/Auckland", 22.0, 8.0)
        self.assertTrue(
            self.Dnd._active_for(self.owner, now=moment),
            "6 h à Auckland est dans une fenêtre de 22 h à 8 h",
        )

    def test_fenetre_de_largeur_nulle_ne_fait_rien_taire(self):
        """Un réglage inachevé n'est pas une fenêtre de 24 h."""
        self._quiet("America/Toronto", 0.0, 0.0)
        self.assertFalse(self.Dnd._active_for(self.owner))

    def test_fuseau_illisible_laisse_parler(self):
        self._quiet("Mars/Olympus_Mons", 0.0, 23.0)
        self.assertFalse(self.Dnd._active_for(self.owner))

    def test_fin_de_fenetre_rendue_en_utc(self):
        moment = fields.Datetime.to_datetime("2026-09-10 04:00:00")
        self._quiet("America/Toronto", 22.0, 8.0)
        end = self.Dnd._quiet_window_end(self.owner, now=moment)
        # 8 h à Montréal le 2026-09-10 = 12:00 UTC.
        self.assertEqual(fields.Datetime.to_string(end), "2026-09-10 12:00:00")

    # ------------------------------------------------------------------
    # 5. La garde du courriel
    # ------------------------------------------------------------------
    def _new_inbound(self, count=1, offset=900):
        BfEmail = self.env["bf.email"].with_user(self.owner)
        recs = BfEmail.browse()
        for index in range(count):
            uid = str(offset + index)
            recs |= BfEmail.create({
                "subject": "Sujet %s" % uid,
                "email_from": "veille@netdata.test",
                "email_to": "owner@test.invalid",
                "direction": "in",
                "status": "new",
                "source": "imap",
                "account_id": self.account.id,
                "user_id": self.owner.id,
                "imap_in_inbox": True,
                "imap_folder": "INBOX",
                "imap_uid": uid,
                "message_id_header": "<dnd-%s@test.invalid>" % uid,
                "date": fields.Datetime.now(),
            })
        return recs

    def test_courriel_retenu_et_note(self):
        self.owner.sudo().bf_dnd_meetings = True
        self._meeting()
        mails = self._new_inbound(count=3)
        self.env["bf.email.popup"]._notify_new_emails(mails)
        self.assertFalse(self._bus("bf_email/popup"),
                         "aucun avis ne doit sortir pendant le mode")
        held = self.Held.search([("user_id", "=", self.owner.id),
                                 ("kind", "=", "mail")])
        self.assertEqual(len(held), 3)
        self.assertFalse(any(held.mapped("released_at")))

    def test_hors_mode_l_avis_sort(self):
        mails = self._new_inbound(count=1)
        self.env["bf.email.popup"]._notify_new_emails(mails)
        self.assertEqual(len(self._bus("bf_email/popup")), 1)
        self.assertFalse(self.Held.search([("user_id", "=", self.owner.id)]))

    def test_retenue_sans_doublon(self):
        """Deux passes de synchro sur la même ligne ne la notent qu'une fois."""
        self.owner.sudo().bf_dnd_meetings = True
        self._meeting()
        mails = self._new_inbound(count=1)
        self.env["bf.email.popup"]._notify_new_emails(mails)
        self.env["bf.email.popup"]._notify_new_emails(mails)
        self.assertEqual(
            len(self.Held.search([("user_id", "=", self.owner.id)])), 1)

    def test_bf_no_popup_ne_surgit_pas_et_n_est_pas_retenu(self):
        """La ligne reste dans la boîte, elle ne fait simplement pas surface."""
        mails = self._new_inbound(count=1)
        mails.sudo().bf_no_popup = True
        self.env["bf.email.popup"]._notify_new_emails(mails)
        self.assertFalse(self._bus("bf_email/popup"))
        self.assertFalse(self.Held.search([("user_id", "=", self.owner.id)]))
        self.assertFalse(mails.is_handled,
                         "« pas d'avis » n'est pas « traité »")

    def test_regle_pose_bf_no_popup(self):
        rule = self.env["bf.email.rule"].with_user(self.owner).create({
            "name": "Battements de la veille",
            "scope": "user",
            "user_id": self.owner.id,
            "match_type": "all",
            "set_no_popup": True,
            "condition_ids": [(0, 0, {
                "field_name": "email_from",
                "operator": "contains",
                "value": "netdata.test",
            })],
        })
        self.assertFalse(rule.is_noop)
        mail = self._new_inbound(count=1, offset=950)
        self.assertTrue(mail.bf_no_popup)
        self.assertFalse(mail.is_handled)

    # ------------------------------------------------------------------
    # 6. La garde du rappel d'agenda
    # ------------------------------------------------------------------
    def _alarm_event(self, minutes_to_start=10, duration=60):
        alarm = self.env["calendar.alarm"].sudo().create({
            "name": "Rappel d'essai",
            "alarm_type": "notification",
            "duration": 15,
            "interval": "minutes",
        })
        now = fields.Datetime.now()
        partners = self.owner.partner_id | self.env["res.partner"].create({
            "name": "Autre participant",
            "email": "autre@test.invalid",
        })
        return self.env["calendar.event"].sudo().create({
            "name": "Reprise de statutaire",
            "start": now + timedelta(minutes=minutes_to_start),
            "stop": now + timedelta(minutes=minutes_to_start + duration),
            "show_as": "busy",
            "partner_ids": [(6, 0, partners.ids)],
            "alarm_ids": [(6, 0, alarm.ids)],
        })

    def _check_alarm(self, event):
        manager = self.env["calendar.alarm_manager"].with_user(self.owner)
        return manager.do_check_alarm_for_one_date(
            event.start, event.with_user(self.owner), 15, 3600 * 24,
            "notification",
        )

    def test_rappel_sort_hors_mode(self):
        event = self._alarm_event()
        self.assertTrue(self._check_alarm(event))

    def test_rappel_retenu_pendant_le_mode(self):
        self.owner.sudo().bf_dnd_manual_until = (
            fields.Datetime.now() + timedelta(hours=1))
        event = self._alarm_event()
        self.assertEqual(self._check_alarm(event), [])
        held = self.Held.search([("user_id", "=", self.owner.id),
                                 ("kind", "=", "reminder")])
        self.assertEqual(len(held), 1)
        self.assertEqual(held.event_name, "Reprise de statutaire")

    def test_rappel_de_demain_ecarte_sans_trace(self):
        """⚠️ `get_next_notif` rend 24 h d'alarmes.

        Un rappel de demain n'a rien à faire dans le résumé d'une rencontre
        qui finit dans dix minutes : il est écarté, et le client le récupère
        au réarmement.
        """
        self.owner.sudo().bf_dnd_manual_until = (
            fields.Datetime.now() + timedelta(hours=1))
        event = self._alarm_event(minutes_to_start=20 * 60)
        self.assertEqual(self._check_alarm(event), [])
        self.assertFalse(self.Held.search([("user_id", "=", self.owner.id),
                                           ("kind", "=", "reminder")]))

    # ------------------------------------------------------------------
    # 7. La bascule, le résumé, et le fait qu'il ne sorte qu'une fois
    # ------------------------------------------------------------------
    def test_bascule_pousse_l_etat_dans_les_deux_sens(self):
        self.owner.sudo().bf_dnd_meetings = True
        event = self._meeting()
        self.Dnd._cron_bf_dnd_tick()
        states = self._bus("bf_dnd/state")
        self.assertEqual(len(states), 1)
        self.assertTrue(states[0]["active"])
        self.assertEqual(states[0]["reason"], "meeting")

        self._mark()
        event.sudo().stop = fields.Datetime.now() - timedelta(minutes=1)
        self.Dnd._cron_bf_dnd_tick()
        states = self._bus("bf_dnd/state")
        self.assertEqual(len(states), 1)
        self.assertFalse(states[0]["active"])

    def test_resume_de_sortie_nomme_la_rencontre_en_premier(self):
        self.owner.sudo().write({
            "bf_dnd_meetings": True,
            "bf_dnd_last_state": True,
        })
        event = self._meeting()
        self.env["bf.email.popup"]._notify_new_emails(self._new_inbound(2))
        self.Dnd._hold_reminder(self.owner, event)
        self._mark()

        event.sudo().stop = fields.Datetime.now() - timedelta(minutes=1)
        self.Dnd._cron_bf_dnd_tick()
        digests = [p for p in self._bus("bf_email/popup")
                   if p.get("kind") == "dnd_digest"]
        self.assertEqual(len(digests), 1)
        self.assertEqual(digests[0]["mail_count"], 2)
        self.assertEqual(digests[0]["meetings"], ["Statutaire d'essai"])
        self.assertTrue(digests[0]["sent_ms"], "l'horloge du serveur manque")

    def test_le_resume_ne_sort_qu_une_fois(self):
        """Un cron à la minute qui répète le résumé est pire que rien."""
        self.owner.sudo().write({
            "bf_dnd_meetings": True,
            "bf_dnd_last_state": True,
        })
        event = self._meeting()
        self.env["bf.email.popup"]._notify_new_emails(self._new_inbound(1))
        event.sudo().stop = fields.Datetime.now() - timedelta(minutes=1)
        self.Dnd._cron_bf_dnd_tick()
        self._mark()
        self.Dnd._cron_bf_dnd_tick()
        self.assertFalse(self._bus("bf_email/popup"))
        self.assertFalse(self._bus("bf_dnd/state"))
        held = self.Held.search([("user_id", "=", self.owner.id)])
        self.assertTrue(all(held.mapped("released_at")))

    def test_le_cron_ne_fait_rien_si_l_instance_est_eteinte(self):
        self.param.set_param("bf_email.dnd_enabled", "0")
        self.owner.sudo().write({"bf_dnd_meetings": True,
                                 "bf_dnd_last_state": True})
        self._meeting()
        self.Dnd._cron_bf_dnd_tick()
        self.assertFalse(self._bus("bf_dnd/state"))
        self.assertTrue(self.owner.sudo().bf_dnd_last_state,
                        "le témoin ne doit pas bouger non plus")

    def test_ecrire_le_reglage_annonce_la_bascule_une_seule_fois(self):
        """Le formulaire des préférences doit basculer comme un bouton."""
        self.owner.with_user(self.owner).write({
            "bf_dnd_manual_until": fields.Datetime.now() + timedelta(hours=1),
        })
        states = self._bus("bf_dnd/state")
        self.assertEqual(len(states), 1)
        self.assertTrue(states[0]["active"])

    def test_le_bouton_n_annonce_pas_deux_fois(self):
        user = self.owner.with_user(self.owner).with_context(minutes=30)
        user.action_bf_dnd_for_minutes()
        self.assertEqual(len(self._bus("bf_dnd/state")), 1)

    # ------------------------------------------------------------------
    # 8. Le battement, tel que le serveur d'actions l'exécute vraiment
    # ------------------------------------------------------------------
    def test_le_battement_tourne_en_superusager(self):
        """🔴 Le défaut vu en production le 2026-09-09, dix minutes après la
        montée.

        ``ir.actions.server.run()`` appelle ``check_access("write")`` sur le
        modèle de l'action AVANT d'exécuter le code. ``bf.dnd`` est ABSTRAIT :
        pas de table, donc pas d'``ir.model.access``, donc personne n'y a le
        droit d'écrire. Sous un usager ordinaire le battement mourait chaque
        minute sur une ``AccessError`` et le mode n'aurait jamais basculé tout
        seul.

        ⚠️ Aucun autre test de ce fichier ne pouvait l'attraper : ils appellent
        ``_cron_bf_dnd_tick()`` en direct et ne passent jamais par le contrôle
        du serveur d'actions.
        """
        cron = self.env.ref("bf_email_management.ir_cron_bf_dnd_tick")
        self.assertEqual(
            cron.user_id, self.env.ref("base.user_root"),
            "le battement doit tourner en superusager, sinon "
            "`check_access` refuse un modèle abstrait",
        )
        # Et la démonstration du refus, pour que la raison reste lisible.
        # ⚠️ En Odoo 18 `ir.cron` ne porte pas `run()` : l'action serveur est un
        # enregistrement à part, atteint par `ir_actions_server_id`. C'est bien
        # elle qui pose `check_access`, pas le cron.
        action = cron.sudo().ir_actions_server_id
        with self.assertRaises(AccessError):
            action.with_user(self.owner).run()

    # ------------------------------------------------------------------
    # 9. Ce que le navigateur demande vraiment
    # ------------------------------------------------------------------
    def test_les_preferences_s_ouvrent(self):
        """🔴 Le défaut signalé en direct le 2026-09-09.

        La sélection de fuseau appelait ``self.env["res.partner"]._tz_get()``.
        ``_tz_get`` est une **fonction de module** d'
        ``odoo.addons.base.models.res_partner``, pas une méthode de modèle :
        l'appel lève une ``AttributeError``.

        ⚠️ Et comme une sélection appelable n'est évaluée qu'au ``fields_get``,
        ce n'est pas le champ qui tombe, c'est la **page entière** des
        Préférences. Ni la montée, ni un ``get_view`` (singulier) ne le voient ;
        seul ``get_views`` (pluriel) passe par ``fields_get``, et c'est ce que
        le client appelle.
        """
        Users = self.env["res.users"].with_user(self.owner)
        description = Users.fields_get(["bf_dnd_quiet_tz"])["bf_dnd_quiet_tz"]
        self.assertTrue(description["selection"], "la liste des fuseaux est vide")
        self.assertIn(("America/Toronto", "America/Toronto"),
                      description["selection"])
        # Le vrai chemin du navigateur, celui qui rendait un RPC_ERROR.
        Users.get_views([
            (self.env.ref("base.view_users_form_simple_modif").id, "form"),
        ])

    def test_l_etat_pour_la_barre_du_haut(self):
        """Ce que lit la bascule du menu de la photo de profil."""
        etat = self.env["res.users"].with_user(self.owner).bf_dnd_state()
        self.assertTrue(etat["enabled"])
        self.assertFalse(etat["active"])
        self.owner.sudo().bf_dnd_manual_until = (
            fields.Datetime.now() + timedelta(minutes=30))
        etat = self.env["res.users"].with_user(self.owner).bf_dnd_state()
        self.assertTrue(etat["active"])
        self.assertTrue(etat["until"])
        self.assertEqual(etat["reason"], "interrupteur manuel")

    def test_l_etat_est_cache_quand_l_instance_est_eteinte(self):
        """Qui n'a rien allumé ne doit pas voir un interrupteur inerte."""
        self.param.set_param("bf_email.dnd_enabled", "0")
        etat = self.env["res.users"].with_user(self.owner).bf_dnd_state()
        self.assertFalse(etat["enabled"])

    def test_l_etat_ne_lit_que_soi(self):
        """⚠️ Méthode publique, donc appelable par RPC : elle ne prend aucun
        identifiant plutôt que d'avoir à refuser le compte d'autrui."""
        self.owner.sudo().bf_dnd_manual_until = (
            fields.Datetime.now() + timedelta(minutes=30))
        etat = self.env["res.users"].with_user(self.stranger).bf_dnd_state()
        self.assertFalse(etat["active"], "l'état d'un autre a fuité")

    # ------------------------------------------------------------------
    # 10. « Indéfiniment »
    # ------------------------------------------------------------------
    def test_indefiniment_arme_sans_echeance(self):
        """⚠️ Un booléen, pas une date lointaine.

        Une échéance en 2099 se lirait comme un réglage accidentel, et le
        résumé de sortie n'arriverait jamais.
        """
        self.owner.with_user(self.owner).action_bf_dnd_forever()
        etat = self.Dnd._state_for(self.owner)
        self.assertTrue(etat["active"])
        self.assertEqual(etat["reason"], "manual")
        self.assertFalse(etat["until"], "sans échéance veut dire sans échéance")
        self.assertFalse(self.owner.sudo().bf_dnd_manual_until,
                         "l'échéance doit être effacée, pas laissée à côté")

    def test_indefiniment_survit_au_temps_qui_passe(self):
        """Le battement d'une heure plus tard ne doit pas l'éteindre."""
        self.owner.with_user(self.owner).action_bf_dnd_forever()
        plus_tard = fields.Datetime.now() + timedelta(days=3)
        self.assertTrue(self.Dnd._active_for(self.owner, now=plus_tard))

    def test_une_duree_efface_l_indefini(self):
        user = self.owner.with_user(self.owner)
        user.action_bf_dnd_forever()
        user.with_context(minutes=15).action_bf_dnd_for_minutes()
        self.assertFalse(self.owner.sudo().bf_dnd_manual_forever)
        self.assertTrue(self.Dnd._state_for(self.owner)["until"])

    def test_eteindre_leve_aussi_l_indefini(self):
        user = self.owner.with_user(self.owner)
        user.action_bf_dnd_forever()
        user.action_bf_dnd_off()
        self.assertFalse(self.owner.sudo().bf_dnd_manual_forever)
        self.assertFalse(self.Dnd._active_for(self.owner))

    def test_l_etat_dit_sans_echeance_a_la_barre_du_haut(self):
        self.owner.with_user(self.owner).action_bf_dnd_forever()
        etat = self.env["res.users"].with_user(self.owner).bf_dnd_state()
        self.assertTrue(etat["active"])
        self.assertFalse(etat["until"])
        self.assertEqual(etat["reason"], "interrupteur manuel")

    # ------------------------------------------------------------------
    # 11. Le témoin sur la photo de profil
    # ------------------------------------------------------------------
    def test_les_actifs_du_temoin_existent(self):
        """⚠️ Un chemin d'actif faux ne fait PAS échouer la montée.

        Odoo se contente d'un avertissement dans les journaux, le paquet se
        construit sans le fichier, et le témoin ne s'affiche jamais sans que
        rien ne le dise. C'est le seul défaut de ce lot qui serait muet.
        """
        from odoo.modules.module import get_module_path
        import os

        racine = get_module_path("bf_email_management")
        manifeste = self.env["ir.module.module"].sudo().search(
            [("name", "=", "bf_email_management")], limit=1)
        self.assertTrue(manifeste)
        attendus = [
            "static/src/js/bf_dnd_usermenu.js",
            "static/src/xml/bf_dnd_usermenu.xml",
            "static/src/scss/bf_dnd_usermenu.scss",
        ]
        for rel in attendus:
            self.assertTrue(os.path.isfile(os.path.join(racine, rel)),
                            "actif déclaré mais absent du disque : %s" % rel)

    def test_le_paquet_backend_porte_le_temoin(self):
        """Le vrai contrôle : ce que le navigateur reçoit.

        ⚠️ Ne PAS interroger `/web/assets/any/...` : cette URL sert
        l'attachement en cache et ne reconstruit rien, donc elle rendrait
        l'ancien paquet et on conclurait à tort que la ligne d'actif est
        perdue.
        """
        paquet = self.env["ir.qweb"]._get_asset_bundle(
            "web.assets_backend", assets_params={})
        js = (paquet.js().raw or b"").decode("utf-8", "replace")
        self.assertIn("o_bf_dnd_on", js)
        self.assertIn("UserMenuDnd", js)
