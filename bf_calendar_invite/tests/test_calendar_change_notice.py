"""What a guest is told when a meeting moves, and what they are not told.

The measurements behind this feature that these tests hold in place:

* a meeting that is LENGTHENED tells its guests. Core notifies on `start` and
  on nothing else, which is how a statutory meeting with three outside guests
  went from thirty to sixty minutes with nobody written to;
* the notice describes the change against WHAT THE GUEST HOLDS, not against the
  value the meeting had a minute ago. Two moves in a row are one message;
* a change learned from another calendar leaves on its own, and only when the
  parameter arming it is on;
* a change made here waits for someone;
* core's own notice is stood down, so nothing goes out twice.
"""

from datetime import datetime, timedelta

from odoo import fields
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestCalendarChangeNotice(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.guest = cls.env["res.partner"].create({
            "name": "Guest Outside",
            "email": "guest@example.org",
        })
        cls.mute = cls.env["res.partner"].create({
            "name": "Guest Without Address",
        })
        cls.organiser = cls.env.user.partner_id

    def _event(self, **overrides):
        # ⚠️ Dates relatives à maintenant, jamais en dur. Un essai qui fixe une
        # date se périme, et celui-ci juge précisément « la rencontre est-elle
        # encore devant nous ».
        start = fields.Datetime.now() + timedelta(days=7)
        vals = {
            "name": "Statutaire de banc",
            "start": start,
            "stop": start + timedelta(minutes=30),
            "partner_ids": [(6, 0, [self.guest.id, self.organiser.id])],
        }
        vals.update(overrides)
        return self.env["calendar.event"].create(vals)

    def _arm(self, value=True):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_calendar_invite.auto_change_notice", "1" if value else "0"
        )

    # --------------------------------------------------------------
    # Ce qui compte comme un changement
    # --------------------------------------------------------------

    def test_allonger_une_rencontre_doit_un_avis(self):
        """La fin bouge, le début non : le cœur se tait, nous pas.

        C'est le cas exact d'une rencontre statutaire mesurée en production.
        """
        event = self._event()
        event.write({"stop": event.stop + timedelta(minutes=30)})
        self.assertTrue(event._bf_change_notice_due())
        lignes = event.bf_change_lines()
        self.assertEqual(len(lignes), 1)
        self.assertNotEqual(lignes[0]["before"], lignes[0]["after"])

    def test_changer_le_lieu_et_le_lien_doit_un_avis(self):
        event = self._event()
        event.write({"location": "Salle du fond"})
        self.assertTrue(event._bf_change_notice_due())
        event.bf_ics_sequence_notified = event.bf_ics_sequence
        event.bf_change_baseline = False
        event.write({"videocall_location": "https://example.org/salle"})
        self.assertTrue(event._bf_change_notice_due())

    def test_une_note_retouchee_ne_doit_rien(self):
        """`description` n'est pas un champ matériel, et ne doit pas le devenir.

        Une coquille corrigée dans les notes n'est pas un déplacement ; faire
        bouger la révision pour ça apprend aux clients d'agenda à redemander
        aux invités pour rien.
        """
        event = self._event()
        avant = event.bf_ics_sequence
        event.write({"description": "<p>Une précision</p>"})
        self.assertEqual(event.bf_ics_sequence, avant)
        self.assertFalse(event._bf_change_notice_due())

    def test_ecrire_la_meme_valeur_ne_doit_rien(self):
        event = self._event()
        avant = event.bf_ics_sequence
        event.write({"start": event.start, "name": event.name})
        self.assertEqual(event.bf_ics_sequence, avant)
        self.assertFalse(event._bf_change_notice_due())

    def test_aller_et_retour_ne_doit_rien(self):
        """Deux révisions, et plus rien à dire : le calendrier du guest est juste.

        La révision a bougé deux fois, mais ce que l'invité tient correspond de
        nouveau. Un avis ici serait un courriel qui annonce l'absence de
        changement.
        """
        event = self._event()
        origine = event.start
        event.write({"start": origine + timedelta(hours=1)})
        event.write({"start": origine})
        self.assertEqual(event.bf_ics_sequence, 2)
        self.assertEqual(event.bf_change_lines(), [])
        self.assertFalse(event._bf_change_notice_due())

    def test_deux_deplacements_se_lisent_depuis_l_origine(self):
        """Le « avant » est ce que l'invité tient, pas l'étape intermédiaire."""
        event = self._event()
        origine = event.start
        event.write({"start": origine + timedelta(hours=1)})
        event.write({"start": origine + timedelta(hours=2)})
        lignes = event.bf_change_lines()
        self.assertEqual(len(lignes), 1)
        attendu = event._bf_change_format("start", origine)
        self.assertEqual(lignes[0]["before"], attendu)

    # --------------------------------------------------------------
    # Qui reçoit, et quand rien n'est dû
    # --------------------------------------------------------------

    def test_l_organisateur_n_est_pas_un_destinataire(self):
        event = self._event()
        self.assertNotIn(self.organiser, event._bf_notice_recipients())
        self.assertIn(self.guest, event._bf_notice_recipients())

    def test_un_invite_sans_adresse_n_est_pas_compte(self):
        event = self._event(
            partner_ids=[(6, 0, [self.mute.id, self.organiser.id])]
        )
        event.write({"stop": event.stop + timedelta(minutes=30)})
        self.assertFalse(event._bf_notice_recipients())
        self.assertFalse(event._bf_change_notice_due())

    def test_une_rencontre_passee_ne_doit_rien(self):
        passee = fields.Datetime.now() - timedelta(days=2)
        event = self._event(start=passee, stop=passee + timedelta(minutes=30))
        event.write({"stop": passee + timedelta(hours=1)})
        self.assertGreater(event.bf_ics_sequence, 0)
        self.assertFalse(event._bf_change_notice_due())

    def test_une_rencontre_annulee_ne_doit_rien(self):
        """L'annulation a son propre avis, et les deux se contrediraient."""
        event = self._event()
        event.write({"stop": event.stop + timedelta(minutes=30)})
        self.assertTrue(event._bf_change_notice_due())
        event.write({"bf_event_status": "cancelled"})
        self.assertFalse(event._bf_change_notice_due())

    # --------------------------------------------------------------
    # Le départ
    # --------------------------------------------------------------

    def test_un_changement_fait_ici_attend_quelqu_un(self):
        self._arm(True)
        event = self._event()
        avant = self.env["mail.mail"].search_count([])
        event.write({"stop": event.stop + timedelta(minutes=30)})
        self.assertEqual(self.env["mail.mail"].search_count([]), avant)
        self.assertTrue(event._bf_change_notice_due())

    def test_un_changement_venu_d_ailleurs_part_tout_seul(self):
        self._arm(True)
        event = self._event()
        event.with_context(bf_remote_change=True).write(
            {"stop": event.stop + timedelta(minutes=30)}
        )
        self.assertFalse(event._bf_change_notice_due())
        self.assertEqual(event.bf_ics_sequence_notified, event.bf_ics_sequence)
        self.assertFalse(event.bf_change_baseline)

    def test_desarme_rien_ne_part(self):
        """🔴 L'état sûr est DÉSARMÉ, et un ICP absent doit y retomber.

        `get_param` d'une clé absente rend `False`. Un module qui se met à
        écrire à des clients à la minute où il s'installe n'a pas de premier
        jour sûr.
        """
        self.env["ir.config_parameter"].sudo().search([
            ("key", "=", "bf_calendar_invite.auto_change_notice")
        ]).unlink()
        event = self._event()
        event.with_context(bf_remote_change=True).write(
            {"stop": event.stop + timedelta(minutes=30)}
        )
        self.assertTrue(event._bf_change_notice_due())
        self._arm(False)
        event2 = self._event()
        event2.with_context(bf_remote_change=True).write(
            {"stop": event2.stop + timedelta(minutes=30)}
        )
        self.assertTrue(event2._bf_change_notice_due())

    def _messages(self, event):
        return self.env["mail.message"].search_count([
            ("model", "=", "calendar.event"), ("res_id", "=", event.id),
        ])

    def test_l_avis_du_coeur_est_mis_a_l_ecart(self):
        """Sinon un déplacement d'heure écrit deux fois à tout le monde.

        🔴 Jugé sur le MESSAGE écrit, pas sur ce que la méthode rend :
        `_send_mail_to_attendees` du cœur ne rend rien du tout (pas de `return`),
        donc un `assertFalse` sur son résultat passe aussi bien quand la mise à
        l'écart est retirée. Attrapé par mutation, pas par lecture.
        """
        event = self._event()
        gabarit = self.env.ref("calendar.calendar_template_meeting_changedate")
        avant = self._messages(event)
        event.attendee_ids._send_mail_to_attendees(gabarit, force_send=True)
        self.assertEqual(
            self._messages(event), avant,
            "l'avis du cœur est parti en plus du nôtre",
        )
        # Et la preuve que le compte discrimine : le même appel, avec la
        # dérogation, écrit bien quelque chose. Sans cette moitié, un compteur
        # qui ne bouge jamais rendrait le même vert.
        event.attendee_ids.with_context(
            bf_allow_core_changedate=True
        )._send_mail_to_attendees(gabarit, force_send=True)
        self.assertGreater(self._messages(event), avant)

    def test_deplacer_l_heure_n_ecrit_pas_deux_fois(self):
        """Le chemin réel : le cœur poste depuis SON write, pas sur appel.

        C'est ce chemin-là qui écrivait aux invités jusqu'ici, et c'est celui
        qu'un essai qui appelle la méthode à la main ne traverse pas.
        """
        event = self._event()
        avant = self._messages(event)
        event.write({"start": event.start + timedelta(hours=1)})
        self.assertEqual(self._messages(event), avant)
        self.assertTrue(event._bf_change_notice_due())

    def test_envoyer_pose_le_repere_et_efface_la_reference(self):
        event = self._event()
        event.write({"stop": event.stop + timedelta(minutes=30)})
        envoyes = event._bf_send_change_notice()
        self.assertTrue(envoyes)
        self.assertEqual(event.bf_ics_sequence_notified, event.bf_ics_sequence)
        self.assertFalse(event.bf_change_baseline)
        self.assertFalse(event._bf_change_notice_due())
        self.assertIn(self.guest, envoyes.recipient_ids)
        self.assertNotIn(self.organiser, envoyes.recipient_ids)

    def test_le_message_rendu_porte_l_avant_et_l_apres(self):
        """Le gabarit se rend pour de vrai, pas seulement ses aides.

        🔴 La chaîne de rendu des courriels de BF traverse `mail_debrand` ; un
        essai qui n'appelle que `bf_change_lines()` ne prouve pas que le
        gabarit sort. Et le QWeb d'un `mail.template` refuse toute méthode
        privée en rendant « 'NoneType' object is not callable », donc en
        n'envoyant plus rien du tout, à personne.
        """
        event = self._event()
        origine = event.stop
        event.write({"stop": origine + timedelta(minutes=30)})
        gabarit = self.env.ref("bf_calendar_invite.mail_template_calendar_change")
        rendu = gabarit._render_field("body_html", event.ids)[event.id]
        self.assertNotIn("NoneType", rendu)
        self.assertIn(event._bf_change_format("stop", origine), rendu)
        self.assertIn(event._bf_change_format("stop", event.stop), rendu)

    def test_la_recherche_trouve_ce_qui_est_du(self):
        event = self._event()
        event.write({"stop": event.stop + timedelta(minutes=30)})
        trouves = self.env["calendar.event"].search([
            ("bf_change_notice_due", "=", True)
        ])
        self.assertIn(event, trouves)
        event._bf_send_change_notice()
        trouves = self.env["calendar.event"].search([
            ("bf_change_notice_due", "=", True)
        ])
        self.assertNotIn(event, trouves)

    def test_l_ics_porte_la_revision(self):
        """Le .ics de l'avis doit se déclarer plus récent que l'invitation."""
        event = self._event()
        event.write({"stop": event.stop + timedelta(minutes=30)})
        ics = event._get_ics_file()[event.id].decode("utf-8")
        self.assertIn("SEQUENCE:%d" % event.bf_ics_sequence, ics)
        self.assertIn("UID:%s" % event._bf_ics_uid_get(), ics)
