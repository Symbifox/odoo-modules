"""Le rappel de confirmation de la veille.

⚠️ Chaque cas porte sa contre-épreuve : sans elle, un essai qui passe ne dit
pas si la porte discrimine ou si elle laisse tout passer. Les pièges visés sont
ceux déjà payés ailleurs dans cette base : la fenêtre sans borne haute qui
rejoue un « à demain » deux heures avant le but, l'ICP absent que `float()`
ramène à zéro sans rien lever, et le courriel au client signé du robot.
"""

import datetime
from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestConfirmReminder(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.etiquette_armee = cls.env["calendar.event.type"].create({
            "name": "Visite de membre (essai)",
            "bf_confirm_reminder": True,
            "bf_confirm_reminder_note": "Préparez votre accès à Internet.",
        })
        cls.etiquette_nue = cls.env["calendar.event.type"].create({
            "name": "Rencontre ordinaire (essai)",
        })
        cls.etiquette_sans_ligne = cls.env["calendar.event.type"].create({
            "name": "Armée sans consigne (essai)",
            "bf_confirm_reminder": True,
        })
        cls.invite = cls.env["res.partner"].create({
            "name": "Benoit Essai",
            "email": "benoit.essai@example.org",
        })
        cls.collegue = cls.env["res.users"].create({
            "name": "Collègue Essai",
            "login": "collegue.essai@example.org",
            "email": "collegue.essai@example.org",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
        })
        cls.organisateur = cls.env["res.users"].create({
            "name": "Organisateur Essai",
            "login": "organisateur.essai@example.org",
            "email": "organisateur.essai@example.org",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
        })

    # -- fabrique ----------------------------------------------------

    def _rencontre(self, dans_heures=23, etiquettes=None, invites=None,
                   allday=False, organisateur=None, **extra):
        debut = fields.Datetime.now() + timedelta(hours=dans_heures)
        etiquettes = (etiquettes if etiquettes is not None
                      else self.etiquette_armee)
        invites = invites if invites is not None else self.invite
        vals = {
            "name": "Rencontre d'essai",
            "start": debut,
            "stop": debut + timedelta(hours=1),
            "allday": allday,
            "user_id": (organisateur or self.organisateur).id,
            "partner_ids": [(6, 0, invites.ids)],
            "categ_ids": [(6, 0, etiquettes.ids)] if etiquettes else [(5, 0, 0)],
        }
        vals.update(extra)
        return self.env["calendar.event"].create(vals)

    def _passe(self):
        """Joue le cron et rend le nombre de courriels qu'il a mis en file."""
        avant = self.env["mail.mail"].sudo().search_count([])
        self.env["calendar.event"]._cron_bf_confirm_reminders()
        return self.env["mail.mail"].sudo().search_count([]) - avant

    # -- 1. le cas qui doit partir, et sa contre-épreuve -------------

    def test_etiquette_armee_part_et_marque(self):
        ev = self._rencontre()
        self.assertTrue(ev.bf_confirm_reminder_armed)
        self.assertEqual(self._passe(), 1, "le rappel aurait dû partir")
        self.assertTrue(ev.bf_confirm_reminder_sent,
                        "la marque aurait dû être posée dans le même savepoint")

    def test_etiquette_nue_ne_part_pas(self):
        ev = self._rencontre(etiquettes=self.etiquette_nue)
        self.assertFalse(ev.bf_confirm_reminder_armed)
        self.assertEqual(self._passe(), 0,
                         "une étiquette non armée ne doit rien envoyer")
        self.assertFalse(ev.bf_confirm_reminder_sent)

    def test_sans_etiquette_ne_part_pas(self):
        ev = self._rencontre(etiquettes=self.env["calendar.event.type"])
        self.assertEqual(self._passe(), 0)
        self.assertFalse(ev.bf_confirm_reminder_sent)

    # -- 2. la fenêtre, bornée en haut ------------------------------

    def test_hors_tolerance_ne_part_pas(self):
        """Une rencontre dans une heure est passée sous la borne de tolérance.

        C'est le piège mesuré sur les rappels de rendez-vous : sans borne
        haute, une reprise après panne envoie « à demain » deux heures avant.
        """
        ev = self._rencontre(dans_heures=1)
        self.assertEqual(self._passe(), 0,
                         "hors tolérance, rien ne doit partir")
        self.assertFalse(ev.bf_confirm_reminder_sent)

    def test_dans_la_tolerance_part(self):
        ev = self._rencontre(dans_heures=19)
        self.assertEqual(self._passe(), 1)
        self.assertTrue(ev.bf_confirm_reminder_sent)

    def test_trop_loin_ne_part_pas(self):
        ev = self._rencontre(dans_heures=72)
        self.assertEqual(self._passe(), 0)
        self.assertFalse(ev.bf_confirm_reminder_sent)

    def test_deja_passee_ne_part_pas(self):
        ev = self._rencontre(dans_heures=-2)
        self.assertEqual(self._passe(), 0)
        self.assertFalse(ev.bf_confirm_reminder_sent)

    # -- 3. une seule fois -------------------------------------------

    def test_une_seule_fois(self):
        ev = self._rencontre()
        self.assertEqual(self._passe(), 1)
        marque = ev.bf_confirm_reminder_sent
        self.assertEqual(self._passe(), 0, "le rappel est reparti une 2e fois")
        self.assertEqual(ev.bf_confirm_reminder_sent, marque)

    # -- 4. à qui, et à qui pas --------------------------------------

    def test_sans_invite_externe_ne_part_pas(self):
        ev = self._rencontre(invites=self.collegue.partner_id)
        self.assertEqual(self._passe(), 0,
                         "un collègue n'est pas un invité à relancer")
        self.assertFalse(ev.bf_confirm_reminder_sent)

    def test_toute_la_journee_ne_part_pas(self):
        ev = self._rencontre(allday=True)
        self.assertEqual(self._passe(), 0)
        self.assertFalse(ev.bf_confirm_reminder_sent)

    def test_annulee_ne_part_pas(self):
        ev = self._rencontre()
        ev.bf_event_status = "cancelled"
        self.assertEqual(self._passe(), 0)
        self.assertFalse(ev.bf_confirm_reminder_sent)

    # -- 5. l'ICP absent n'est pas l'ICP à zéro ----------------------

    def test_icp_absent_rend_le_defaut(self):
        """🔴 `get_param` d'une clé absente rend False, et float(False) = 0.0."""
        self.env["ir.config_parameter"].sudo().search([
            ("key", "=", "bf_calendar_invite.confirm_reminder_hours"),
        ]).unlink()
        self.assertEqual(
            self.env["calendar.event"]._bf_confirm_reminder_hours(), 24.0,
            "non réglé doit valoir 24 h, pas 0",
        )

    def test_icp_a_zero_eteint(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_calendar_invite.confirm_reminder_hours", "0")
        ev = self._rencontre()
        self.assertEqual(self._passe(), 0,
                         "réglé à 0 est un choix : rien ne part")
        self.assertFalse(ev.bf_confirm_reminder_sent)

    def test_icp_illisible_rend_le_defaut(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_calendar_invite.confirm_reminder_hours", "demain")
        self.assertEqual(
            self.env["calendar.event"]._bf_confirm_reminder_hours(), 24.0)

    def test_tolerance_plafonnee_au_preavis(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_calendar_invite.confirm_reminder_hours", "2")
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_calendar_invite.confirm_reminder_grace_hours", "99")
        tot = self._rencontre(dans_heures=1)
        self.assertEqual(self._passe(), 1,
                         "avec un préavis de 2 h, une rencontre dans 1 h est due")
        self.assertTrue(tot.bf_confirm_reminder_sent)

    # -- 6. l'expéditeur, jamais le robot ---------------------------

    def test_organisateur_robot_ne_part_pas(self):
        robot = self.env.ref("base.user_root")
        ev = self._rencontre(organisateur=robot)
        ev.sudo().write({"partner_ids": [(6, 0, self.invite.ids)]})
        self.assertEqual(self._passe(), 0,
                         "mieux vaut rien que signé du robot")
        self.assertFalse(ev.bf_confirm_reminder_sent)

    def test_expediteur_est_l_organisateur(self):
        ev = self._rencontre()
        self.assertEqual(self._passe(), 1)
        courriel = self.env["mail.mail"].sudo().search(
            [], order="id desc", limit=1)
        self.assertIn("organisateur.essai@example.org", courriel.email_from or "")

    # -- 7. le « C » se pose à la réponse, pas à l'envoi -------------

    def test_envoi_ne_pose_pas_le_c(self):
        ev = self._rencontre()
        avant = ev.bf_event_status
        self.assertEqual(self._passe(), 1)
        self.assertEqual(ev.bf_event_status, avant,
                         "le C ne se pose pas au départ du rappel")

    def test_reponse_pose_le_c(self):
        ev = self._rencontre()
        attendee = ev.attendee_ids.filtered(
            lambda a: a.partner_id == self.invite)
        self.assertTrue(attendee)
        attendee.sudo().do_accept()
        self.assertEqual(ev.bf_event_status, "confirmed")

    def test_reponse_sur_rencontre_non_armee_ne_pose_rien(self):
        ev = self._rencontre(etiquettes=self.etiquette_nue)
        ev.sudo().write({"bf_event_status": "tentative"})
        attendee = ev.attendee_ids.filtered(
            lambda a: a.partner_id == self.invite)
        attendee.sudo().do_accept()
        self.assertEqual(
            ev.bf_event_status, "tentative",
            "hors armement, le statut reste celui de l'organisateur",
        )

    # -- 8. la consigne vient de l'étiquette ------------------------

    def test_la_consigne_vient_de_l_etiquette(self):
        ev = self._rencontre()
        self.assertEqual(ev.bf_confirm_note(),
                         "Préparez votre accès à Internet.")

    def test_etiquette_armee_sans_consigne(self):
        ev = self._rencontre(etiquettes=self.etiquette_sans_ligne)
        self.assertFalse(ev.bf_confirm_note(),
                         "une étiquette sans ligne ne doit pas en inventer")

    # -- 9. le sujet ne dit « demain » que si c'est vrai ------------

    def test_sujet_demain(self):
        """Affirmé, jamais conditionnel : un `if` autour d'une affirmation
        rend l'essai vert par abstention quand la condition est fausse."""
        from .test_calendar_invite import _company_tz
        _company_tz(self.env, "America/Montreal")
        # 14 h demain, heure de Montréal : « demain » est vrai quelle que soit
        # l'heure de la passe.
        import pytz
        tz = pytz.timezone("America/Montreal")
        demain = (datetime.datetime.now(tz) + timedelta(days=1)).replace(
            hour=14, minute=0, second=0, microsecond=0)
        debut = demain.astimezone(pytz.utc).replace(tzinfo=None)
        ev = self._rencontre(dans_heures=1)
        ev.write({"start": debut, "stop": debut + timedelta(hours=1)})
        self.assertTrue(ev.bf_confirm_tomorrow())
        self.assertIn("tomorrow", (ev.bf_confirm_subject or "").lower())

    def test_sujet_du_jour_ne_dit_pas_demain(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_calendar_invite.confirm_reminder_hours", "2")
        ev = self._rencontre(dans_heures=1)
        self.assertFalse(ev.bf_confirm_tomorrow(),
                         "une rencontre dans une heure n'est pas demain")
        self.assertNotIn("tomorrow", (ev.bf_confirm_subject or "").lower())

    # -- 10. une rencontre fautive n'emporte pas les autres ---------

    def test_une_fautive_n_emporte_pas_les_autres(self):
        """Le point de reprise par rencontre, mesuré plutôt que supposé."""
        bonne = self._rencontre()
        fautive = self._rencontre()
        # Une rencontre dont le rendu lèvera : plus d'invité joignable au
        # moment du rendu, mais l'armement et la fenêtre tiennent.
        self.env.flush_all()
        # La fonction NON liée, prise sur la classe : `self.env[...].send_mail`
        # est liée à un ensemble vide, et la rappeler décalerait les arguments.
        reel = type(self.env["mail.template"]).send_mail

        def casse(gabarit, res_id, **kw):
            if res_id == fautive.id:
                raise ValueError("rendu impossible (essai)")
            return reel(gabarit, res_id, **kw)

        self.patch(type(self.env["mail.template"]), "send_mail", casse)
        self.env["calendar.event"]._cron_bf_confirm_reminders()
        self.assertTrue(bonne.bf_confirm_reminder_sent,
                        "la bonne rencontre doit partir malgré la fautive")
        self.assertFalse(fautive.bf_confirm_reminder_sent,
                         "la fautive reste à relancer")


@tagged("post_install", "-at_install")
class TestConfirmReminderFrancais(TransactionCase):
    """Le rappel doit arriver en français chez un invité francophone.

    Le corps est écrit en anglais et traduit par `i18n/fr_CA.po` : un terme
    oublié au catalogue part en anglais, sans bruit. C'est le défaut qui a
    atteint l'avis de changement en 6.0.0.
    """

    def test_le_corps_est_traduit(self):
        from .test_calendar_invite import _speak_french
        _speak_french(self.env)
        champ = self.env["ir.ui.view"]._fields["arch_db"]
        vue = self.env.ref("bf_calendar_invite.mail_body_calendar_confirm")
        termes = champ.get_trans_terms(vue.with_context(lang="fr_CA").arch_db)
        for anglais in ("Hello", "Hello,", "Thanks,<br/>", "Confirm or cancel",
                        "Here is the link to join:",
                        "A quick reminder that we are still meeting tomorrow at",
                        "A quick reminder that we are still meeting tomorrow.",
                        "A quick reminder that we are still meeting on",
                        "A quick reminder about our meeting."):
            with self.subTest(terme=anglais):
                self.assertNotIn(
                    anglais, termes,
                    "ce terme part encore en anglais chez un invité francophone",
                )

    def test_le_sujet_est_traduit(self):
        from .test_calendar_invite import _speak_french
        _speak_french(self.env)
        invite = self.env["res.partner"].create({
            "name": "Invitée Francophone",
            "email": "francophone@example.org",
            "lang": "fr_CA",
        })
        debut = fields.Datetime.now() + timedelta(hours=23)
        ev = self.env["calendar.event"].create({
            "name": "Visite d'essai",
            "start": debut,
            "stop": debut + timedelta(hours=1),
            "partner_ids": [(6, 0, invite.ids)],
        })
        sujet = ev.with_context(lang="fr_CA").bf_confirm_subject
        self.assertNotIn("See you", sujet or "")
        self.assertIn("On se voit", sujet or "")


@tagged("post_install", "-at_install")
class TestConfirmReminderStatutInitial(TransactionCase):
    """Une rencontre armée naît TENTATIVE, sinon le « C » ne dit plus rien.

    Trouvé en rendant le courriel, pas en lisant le code : la rencontre
    naissait « confirmed » par le défaut du module, donc le badge était déjà
    posé avant que l'invité ait répondu.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.armee = cls.env["calendar.event.type"].create({
            "name": "Armée (essai statut)",
            "bf_confirm_reminder": True,
        })
        cls.nue = cls.env["calendar.event.type"].create({
            "name": "Nue (essai statut)",
        })

    def _creer(self, etiquettes, **extra):
        debut = fields.Datetime.now() + timedelta(hours=23)
        vals = {
            "name": "Rencontre d'essai de statut",
            "start": debut,
            "stop": debut + timedelta(hours=1),
            "categ_ids": [(6, 0, etiquettes.ids)],
        }
        vals.update(extra)
        return self.env["calendar.event"].create(vals)

    def test_armee_nait_tentative(self):
        self.assertEqual(self._creer(self.armee).bf_event_status, "tentative")

    def test_non_armee_nait_confirmee(self):
        self.assertEqual(self._creer(self.nue).bf_event_status, "confirmed")

    def test_statut_explicite_respecte(self):
        ev = self._creer(self.armee, bf_event_status="confirmed")
        self.assertEqual(
            ev.bf_event_status, "confirmed",
            "un statut passé explicitement est un choix, pas un défaut",
        )

    def test_la_reponse_de_l_invite_pose_le_c(self):
        invite = self.env["res.partner"].create({
            "name": "Invité du statut",
            "email": "statut@example.org",
        })
        ev = self._creer(self.armee, partner_ids=[(6, 0, invite.ids)])
        self.assertEqual(ev.bf_event_status, "tentative")
        ev.attendee_ids.filtered(
            lambda a: a.partner_id == invite).sudo().do_accept()
        self.assertEqual(ev.bf_event_status, "confirmed")


@tagged("post_install", "-at_install")
class TestConfirmReminderRelectureAdverse(TransactionCase):
    """Les constats de la relecture adverse du 2026-11-02, chacun en essai.

    Ils ont tous été MESURÉS sur une base d'essai avant d'être réparés : ces essais sont
    là pour que la réparation ne reparte pas.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.armee = cls.env["calendar.event.type"].create({
            "name": "Armée (relecture)", "bf_confirm_reminder": True,
            "bf_confirm_reminder_note": "Préparez votre accès.",
        })
        cls.nue = cls.env["calendar.event.type"].create({"name": "Nue (relecture)"})
        cls.invite = cls.env["res.partner"].create({
            "name": "Cliente Externe", "email": "cliente@example.org"})
        cls.collegue = cls.env["res.users"].create({
            "name": "Collègue Interne", "login": "collegue.rel@example.org",
            "email": "collegue.rel@example.org",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])]})
        # Un organisateur joignable par défaut : sans lui, le garde-fou
        # d'expéditeur refuse l'envoi et chaque essai mesurerait ce refus au
        # lieu de ce qu'il croit mesurer.
        cls.organisateur = cls.env["res.users"].create({
            "name": "Organisateur Relecture", "login": "orga.rel@example.org",
            "email": "orga.rel@example.org",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])]})

    def _ev(self, **extra):
        debut = fields.Datetime.now() + timedelta(hours=23)
        vals = {"name": "Rencontre relecture", "start": debut,
                "stop": debut + timedelta(hours=1),
                "user_id": self.organisateur.id,
                "categ_ids": [(6, 0, self.armee.ids)],
                "partner_ids": [(6, 0, self.invite.ids)]}
        vals.update(extra)
        return self.env["calendar.event"].create(vals)

    def _passe(self):
        avant = self.env["mail.mail"].sudo().search_count([])
        self.env["calendar.event"]._cron_bf_confirm_reminders()
        return self.env["mail.mail"].sudo().search_count([]) - avant

    # -- B1 : une rencontre annulée ne ressuscite pas ---------------------

    def test_b1_annulee_ne_ressuscite_pas(self):
        ev = self._ev()
        ev.write({"bf_event_status": "cancelled"})
        att = ev.attendee_ids.filtered(lambda a: a.partner_id == self.invite)
        att.sudo().do_accept()
        self.assertEqual(ev.bf_event_status, "cancelled",
                         "un clic sur un vieux rappel ressuscitait la rencontre")

    def test_b1_contre_epreuve_provisoire_se_confirme(self):
        ev = self._ev()
        self.assertEqual(ev.bf_event_status, "tentative")
        ev.attendee_ids.filtered(
            lambda a: a.partner_id == self.invite).sudo().do_accept()
        self.assertEqual(ev.bf_event_status, "confirmed")

    # -- B2 : un lot mêlé n'apparie plus par position --------------------

    def test_b2_lot_mele(self):
        debut = fields.Datetime.now() + timedelta(hours=23)
        commun = {"start": debut, "stop": debut + timedelta(hours=1),
                  "categ_ids": [(6, 0, self.armee.ids)]}
        evs = self.env["calendar.event"].create([
            dict(commun, name="Récurrente explicite", recurrency=True,
                 rrule_type="daily", end_type="count", count=2,
                 bf_event_status="confirmed"),
            dict(commun, name="Simple implicite"),
        ])
        simple = evs.filtered(lambda e: e.name == "Simple implicite")
        explicite = evs.filtered(lambda e: e.name == "Récurrente explicite")
        self.assertEqual(simple.bf_event_status, "tentative",
                         "la bascule a été appariée au mauvais enregistrement")
        self.assertEqual(explicite.bf_event_status, "confirmed",
                         "un statut explicite a été défait")

    # -- B3 : toutes les occurrences d'une série, pas seulement la 1re ----

    def test_b3_serie_entiere_provisoire(self):
        debut = fields.Datetime.now() + timedelta(hours=23)
        base = self.env["calendar.event"].create({
            "name": "Série armée", "start": debut,
            "stop": debut + timedelta(hours=1),
            "categ_ids": [(6, 0, self.armee.ids)],
            "partner_ids": [(6, 0, self.invite.ids)],
            "recurrency": True, "rrule_type": "daily",
            "end_type": "count", "count": 4,
        })
        serie = base.recurrence_id.calendar_event_ids
        self.assertEqual(len(serie), 4)
        statuts = set(serie.mapped("bf_event_status"))
        self.assertEqual(
            statuts, {"tentative"},
            "les occurrences portaient le « C » avant toute réponse : %s" % statuts,
        )

    # -- C1 : le jour est nommé dans le fuseau du message ----------------

    def test_c1_le_jour_est_celui_du_lecteur(self):
        import pytz
        from .test_calendar_invite import _company_tz
        _company_tz(self.env, "America/Montreal")
        tz = pytz.timezone("America/Montreal")
        # 20 h à Montréal, donc le LENDEMAIN 01 h en UTC.
        local = (datetime.datetime.now(tz) + timedelta(days=3)).replace(
            hour=20, minute=0, second=0, microsecond=0)
        debut = local.astimezone(pytz.utc).replace(tzinfo=None)
        ev = self._ev()
        ev.write({"start": debut, "stop": debut + timedelta(hours=1)})
        jour = ev.bf_confirm_day()
        self.assertIn(str(local.day), jour,
                      "le jour annoncé n'est pas celui du lecteur : %s" % jour)
        self.assertNotIn(str(debut.date()), jour,
                         "le jour est écrit en date ISO, pas en jour de semaine")

    # -- C2 : pas de marque sans destinataire joignable ------------------

    def test_c2_invite_sans_adresse(self):
        muet = self.env["res.partner"].create({"name": "Sans adresse"})
        ev = self._ev(partner_ids=[(6, 0, muet.ids)])
        self.assertEqual(self._passe(), 0)
        self.assertFalse(ev.bf_confirm_reminder_sent,
                         "marquée relancée alors que personne n'a été écrit")

    # -- C3 : un collègue qui accepte ne pose pas le « C » ---------------

    def test_c3_collegue_ne_confirme_pas(self):
        ev = self._ev(partner_ids=[(6, 0, (self.invite | self.collegue.partner_id).ids)])
        ev.attendee_ids.filtered(
            lambda a: a.partner_id == self.collegue.partner_id).sudo().do_accept()
        self.assertEqual(ev.bf_event_status, "tentative",
                         "le badge dit « l'invité a répondu », pas « un collègue »")
        ev.attendee_ids.filtered(
            lambda a: a.partner_id == self.invite).sudo().do_accept()
        self.assertEqual(ev.bf_event_status, "confirmed")

    # -- C4 : un collègue archivé n'est pas un invité externe ------------

    def test_c4_collegue_archive(self):
        parti = self.env["res.users"].create({
            "name": "Collègue Parti", "login": "parti.rel@example.org",
            "email": "parti.rel@example.org",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])]})
        ev = self._ev(partner_ids=[(6, 0, (self.invite | parti.partner_id).ids)])
        parti.action_archive()
        ev.invalidate_recordset()
        externes = ev._bf_outside_guests().partner_id
        self.assertEqual(externes, self.invite,
                         "un collègue parti redevenait un invité externe")
        self.assertTrue(ev.bf_invitation_url,
                        "le bouton de confirmation disparaissait")

    # -- C5 et l'expéditeur : le robot, l'archivé, l'étiquette -----------

    def test_c5_organisateur_robot_mais_participant_interne(self):
        """🔴 Le cas réel du client : toutes ses rencontres sont organisées par
        OdooBot, parce qu'elles viennent de la synchronisation d'agenda."""
        robot = self.env.ref("base.user_root")
        ev = self._ev(user_id=robot.id, partner_ids=[
            (6, 0, (self.invite | self.collegue.partner_id).ids)])
        self.assertEqual(self._passe(), 1,
                         "aucun rappel ne partait sur les vraies données")
        courriel = self.env["mail.mail"].sudo().search([], order="id desc", limit=1)
        self.assertIn("collegue.rel@example.org", courriel.email_from or "")

    def test_c5_etiquette_porte_l_expediteur(self):
        robot = self.env.ref("base.user_root")
        self.armee.bf_confirm_reminder_from_id = self.collegue
        autre = self.env["res.users"].create({
            "name": "Autre Interne", "login": "autre.rel@example.org",
            "email": "autre.rel@example.org",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])]})
        ev = self._ev(user_id=robot.id, partner_ids=[
            (6, 0, (self.invite | autre.partner_id).ids)])
        self.assertEqual(self._passe(), 1)
        courriel = self.env["mail.mail"].sudo().search([], order="id desc", limit=1)
        self.assertIn("collegue.rel@example.org", courriel.email_from or "",
                      "l'étiquette doit l'emporter sur les participants")

    def test_c5_organisateur_archive_ne_signe_pas(self):
        parti = self.env["res.users"].create({
            "name": "Organisateur Parti", "login": "orgparti.rel@example.org",
            "email": "orgparti.rel@example.org",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])]})
        ev = self._ev(user_id=parti.id)
        parti.action_archive()
        ev.invalidate_recordset()
        self.assertEqual(self._passe(), 0,
                         "un organisateur parti signait encore le rappel")
        self.assertFalse(ev.bf_confirm_reminder_sent)

    # -- C6 : une tolérance à zéro n'éteint plus tout --------------------

    def test_c6_tolerance_a_zero_envoie_encore(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_calendar_invite.confirm_reminder_grace_hours", "0")
        ev = self._ev()
        ev.write({"start": fields.Datetime.now() + timedelta(hours=24),
                  "stop": fields.Datetime.now() + timedelta(hours=25)})
        self.assertEqual(self._passe(), 1,
                         "une tolérance à 0 éteignait toute la fonction")

    def test_c6_tolerance_negative_envoie_encore(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_calendar_invite.confirm_reminder_grace_hours", "-6")
        ev = self._ev()
        ev.write({"start": fields.Datetime.now() + timedelta(hours=24),
                  "stop": fields.Datetime.now() + timedelta(hours=25)})
        self.assertEqual(self._passe(), 1)


@tagged("post_install", "-at_install")
class TestConfirmReminderReservations(TransactionCase):
    """Jamais deux rappels au même client (constat C7 de la relecture).

    `bf_appointment` envoie déjà son propre rappel de 24 h aux réservations, et
    `calendar.event.type` est UNE SEULE liste partagée avec les types de
    rendez-vous. L'essai ne tourne que là où `resource_booking` est installé :
    ce module n'en dépend pas.
    """

    def setUp(self):
        super().setUp()
        if "resource_booking_ids" not in self.env["calendar.event"]._fields:
            self.skipTest("resource_booking n'est pas installé")

    def test_c7_la_garde_discrimine(self):
        from odoo import Command
        env = self.env(context=dict(self.env.context, tz="UTC"))
        orga = env["res.users"].create({
            "name": "Hôte C7", "login": "hote.c7t@example.org",
            "email": "hote.c7t@example.org",
            "groups_id": [(6, 0, [env.ref("base.group_user").id])]})
        tag = env["calendar.event.type"].create({
            "name": "Étiquette partagée (essai C7)", "bf_confirm_reminder": True,
            "bf_confirm_reminder_from_id": orga.id})
        client = env["res.partner"].create({
            "name": "Cliente C7", "email": "cliente.c7t@example.org"})
        cal = env["resource.calendar"].create({
            "name": "24/7 C7", "tz": "UTC",
            "attendance_ids": [Command.create({
                "name": "J%d" % d, "dayofweek": str(d), "hour_from": 0.0,
                "hour_to": 24.0, "day_period": "morning"}) for d in range(7)]})
        ressource = env["resource.resource"].create({
            "name": "Salle C7", "calendar_id": cal.id,
            "resource_type": "material", "tz": "UTC"})
        comb = env["resource.booking.combination"].create({
            "resource_ids": [Command.set([ressource.id])]})
        btype = env["resource.booking.type"].create({
            "name": "Type C7", "duration": 1.0, "slot_duration": 1.0,
            "modifications_deadline": 0.0, "combination_assignment": "sorted",
            "resource_calendar_id": cal.id,
            "combination_rel_ids": [Command.create({
                "sequence": 0, "combination_id": comb.id})]})
        debut = (fields.Datetime.now() + timedelta(hours=23)).replace(
            minute=0, second=0, microsecond=0)
        resa = env["resource.booking"].create({
            "type_id": btype.id, "partner_ids": [Command.set([client.id])],
            "combination_id": comb.id, "combination_auto_assign": False,
            "start": debut, "duration": 1.0})
        ev = resa.meeting_id
        self.assertTrue(ev, "la réservation doit porter un événement")
        # ⚠️ L'étiquette est posée À LA MAIN : les étiquettes par défaut du type
        # ne se propagent pas à l'événement sur une création programmatique, et
        # sans armement c'est le mauvais filtre qui écarterait l'événement.
        ev.write({"categ_ids": [Command.set([tag.id])]})
        ev.invalidate_recordset()
        self.assertTrue(ev.bf_confirm_reminder_armed,
                        "l'essai ne prouve rien si l'événement n'est pas armé")
        self.assertTrue(ev.resource_booking_ids)

        avant = env["mail.mail"].sudo().search_count([])
        env["calendar.event"]._cron_bf_confirm_reminders()
        self.assertEqual(
            env["mail.mail"].sudo().search_count([]) - avant, 0,
            "deux rappels seraient partis au même client",
        )
        self.assertFalse(ev.bf_confirm_reminder_sent)

        # Contre-épreuve : la même étiquette, sur une rencontre sans réservation.
        d2 = fields.Datetime.now() + timedelta(hours=23)
        ordinaire = env["calendar.event"].create({
            "name": "Rencontre ordinaire C7", "start": d2,
            "stop": d2 + timedelta(hours=1), "user_id": orga.id,
            "partner_ids": [Command.set([client.id])],
            "categ_ids": [Command.set([tag.id])]})
        avant = env["mail.mail"].sudo().search_count([])
        env["calendar.event"]._cron_bf_confirm_reminders()
        self.assertEqual(
            env["mail.mail"].sudo().search_count([]) - avant, 1,
            "la garde écarte plus que les réservations",
        )
        self.assertTrue(ordinaire.bf_confirm_reminder_sent)


@tagged("post_install", "-at_install")
class TestConfirmReminderFuseauEtSignature(TransactionCase):
    """Les deux défauts que la QA des ENVOIS a trouvés, et pas les essais.

    Tous deux venaient du même angle mort : le module avait appris à ne pas
    laisser le ROBOT signer l'en-tête, et ni l'heure ni le bas du corps
    n'avaient suivi.

    1. `event_tz` est vide sur toutes les rencontres venues d'une
       synchronisation d'agenda, donc le fuseau se décidait sur l'horaire de
       travail de la société — qu'Odoo crée à UTC. Une visite de 10 h 30 était
       annoncée « à 15 h 30 » à la cliente.
    2. Le corps signait `object.user_id.signature`, c'est-à-dire la signature
       d'OdooBot : « -- System » au bas d'un courriel parti de l'adresse d'une
       personne.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.signataire = cls.env["res.users"].create({
            "name": "Fabienne Essai",
            "login": "fabienne.essai@example.org",
            "email": "fabienne.essai@example.org",
            "tz": "America/Toronto",
            "signature": "<p>Fabienne Essai, conseillère</p>",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
        })
        cls.sans_signature = cls.env["res.users"].create({
            "name": "Sylvain Essai",
            "login": "sylvain.essai@example.org",
            "email": "sylvain.essai@example.org",
            "tz": "America/Toronto",
            "signature": False,
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
        })
        cls.invite = cls.env["res.partner"].create({
            "name": "Cliente Essai",
            "email": "cliente.essai@example.org",
        })
        cls.armee = cls.env["calendar.event.type"].create({
            "name": "Visites (essai fuseau)",
            "bf_confirm_reminder": True,
            "bf_confirm_reminder_from_id": cls.signataire.id,
        })
        cls.nue = cls.env["calendar.event.type"].create({
            "name": "Ordinaire (essai fuseau)",
        })

    def _horaire_a_utc(self):
        """L'état réel d'un locataire : horaire de travail laissé à UTC."""
        from .test_calendar_invite import _company_tz
        _company_tz(self.env, "UTC")

    def _rencontre(self, etiquettes, **extra):
        """Une rencontre comme la synchro les écrit : `user_id` = le robot,
        `event_tz` vide."""
        debut = fields.Datetime.now() + timedelta(hours=23)
        vals = {
            "name": "Visite d'essai",
            "start": debut,
            "stop": debut + timedelta(hours=1),
            "user_id": self.env.ref("base.user_root").id,
            "partner_ids": [(6, 0, self.invite.ids)],
            "categ_ids": [(6, 0, etiquettes.ids)] if etiquettes else [(5, 0, 0)],
        }
        vals.update(extra)
        ev = self.env["calendar.event"].create(vals)
        self.assertFalse(ev.event_tz, "l'essai doit reproduire une rencontre sans fuseau")
        return ev

    # -- 1. l'heure annoncée ----------------------------------------

    def test_une_heure_en_utc_ne_part_plus_en_silence(self):
        """Le fuseau reste celui du coeur : ce qui change, c'est qu'on le DIT.

        Arbitré le 2026-11-03 : régler les heures de travail de la société est
        la réparation, parce qu'elle vaut pour les cinq courriels du module et
        pour toutes les rencontres. Un candidat de plus dans `_bf_mail_tz`
        n'aurait réparé que les rencontres étiquetées, et deux rencontres du
        même client auraient annoncé des heures différentes.
        """
        self._horaire_a_utc()
        ev = self._rencontre(self.armee)
        self.assertEqual(ev._bf_mail_tz(), "UTC")
        with self.assertLogs("odoo.addons.bf_calendar_invite.models.calendar_event",
                             level="WARNING") as journal:
            self.env["calendar.event"]._cron_bf_confirm_reminders()
        dit = "\n".join(journal.output)
        self.assertIn("en UTC", dit)
        self.assertIn(str(ev.id), dit)
        self.assertIn("heures de travail", dit)

    def test_contre_epreuve_un_horaire_regle_ne_dit_rien(self):
        """Sans cette contre-épreuve, l'avertissement pourrait sortir toujours."""
        from .test_calendar_invite import _company_tz
        _company_tz(self.env, "America/Toronto")
        ev = self._rencontre(self.armee)
        self.assertEqual(ev._bf_mail_tz(), "America/Toronto")
        import logging
        journal = logging.getLogger(
            "odoo.addons.bf_calendar_invite.models.calendar_event")
        with self.assertNoLogs(journal.name, level="WARNING"):
            self.env["calendar.event"]._cron_bf_confirm_reminders()
        self.assertTrue(ev.bf_confirm_reminder_sent, "le rappel est bien parti")

    def test_le_fuseau_de_la_rencontre_passe_devant_sur_une_serie(self):
        """`event_tz`, quand il existe, reste le premier mot.

        🔴 Et il n'existe QUE sur une série : le coeur ne le stocke pas, il le
        calcule depuis `recurrence_id`. Une rencontre non récurrente n'en a
        jamais, ce qui est l'état de la totalité des rencontres mesurées.
        L'essai porte donc sur une série, seul endroit où l'ordre se voit.
        """
        self._horaire_a_utc()
        debut = fields.Datetime.now() + timedelta(hours=23)
        serie = self.env["calendar.event"].create({
            "name": "Série d'essai",
            "start": debut,
            "stop": debut + timedelta(hours=1),
            "user_id": self.env.ref("base.user_root").id,
            "partner_ids": [(6, 0, self.invite.ids)],
            "categ_ids": [(6, 0, self.armee.ids)],
            "recurrency": True,
            "rrule_type": "daily",
            "end_type": "count",
            "count": 2,
            "event_tz": "Europe/Paris",
        })
        self.assertEqual(serie.event_tz, "Europe/Paris",
                         "une série, elle, porte bien un fuseau")
        self.assertEqual(serie._bf_mail_tz(), "Europe/Paris",
                         "et il passe devant les heures de travail")

    def test_une_rencontre_ordinaire_n_a_jamais_de_fuseau(self):
        """Le constat qui justifie tout le correctif, tenu par un essai.

        Si un jour le coeur stocke `event_tz`, cet essai tombe et c'est le
        signal qu'il faut relire l'ordre des candidats.
        """
        ev = self._rencontre(self.armee)
        self.assertFalse(ev.event_tz)
        # Et le coeur ne se contente pas de l'ignorer : il REFUSE de l'écrire
        # sur une rencontre sans récurrence.
        try:
            ev.write({"event_tz": "Europe/Paris"})
        except UserError:
            pass
        else:
            self.fail("le coeur a accepté un fuseau sur une rencontre sans "
                      "récurrence : relire l'ordre des candidats de _bf_mail_tz")
        self.assertFalse(ev.event_tz)

    # -- 2. qui signe le corps --------------------------------------

    def test_le_corps_porte_la_signature_de_l_expediteur(self):
        ev = self._rencontre(self.armee)
        self.assertEqual(ev.bf_confirm_signature(), self.signataire.signature)
        self.assertIn("fabienne.essai@example.org", ev._bf_confirm_sender() or "")

    def test_le_corps_ne_signe_jamais_le_robot(self):
        """La contre-épreuve du défaut : la signature d'OdooBot ne sort pas."""
        robot = self.env.ref("base.user_root")
        robot.sudo().signature = "<span>-- <br>System</span>"
        ev = self._rencontre(self.armee)
        modele = self.env.ref("bf_calendar_invite.mail_template_calendar_confirm")
        rendu = modele._render_field("body_html", ev.ids)[ev.id]
        self.assertIn("Fabienne Essai", rendu)
        self.assertNotIn("System", rendu)

    def test_sans_signature_c_est_le_nom_de_l_expediteur(self):
        self.armee.bf_confirm_reminder_from_id = self.sans_signature
        ev = self._rencontre(self.armee)
        self.assertFalse(ev.bf_confirm_signature())
        self.assertEqual(ev.bf_confirm_signer_name(), "Sylvain Essai")

    def test_sans_aucun_candidat_c_est_la_societe(self):
        """Faute de personne nommable, la société signe, jamais le robot.

        Éprouvé sur la résolution COMMUNE et non sur celle du rappel : le
        rappel, lui, refuse d'envoyer avant même de rendre le corps quand
        personne ne peut signer, donc son repli sur la société est du code que
        l'envoi n'atteint pas. L'avis de changement, qui part sans ce
        garde-fou, l'atteint.
        """
        self.armee.bf_confirm_reminder_from_id = False
        ev = self._rencontre(self.armee)
        ev.sudo().write({"partner_ids": [(6, 0, self.invite.ids)]})
        self.assertFalse(ev._bf_mail_signer_partner())
        self.assertEqual(ev.bf_mail_signer_name(), self.env.company.name)
        self.assertFalse(ev.bf_mail_signature())

    def test_un_expediteur_archive_ne_signe_plus(self):
        """Relevé par la relecture adverse : archiver un usager laisse son
        partenaire actif, donc l'étiquette d'un employé parti faisait encore
        partir le rappel de son adresse, Reply-To compris."""
        self.signataire.sudo().action_archive()
        ev = self._rencontre(self.armee)
        partner = ev._bf_confirm_sender_partner()
        self.assertNotEqual(partner, self.signataire.partner_id)
        self.assertNotIn("fabienne.essai@example.org", ev._bf_confirm_sender() or "")

    def test_l_avis_de_changement_ne_signe_pas_le_robot(self):
        """Le seul des quatre autres corps qui part SANS qu'une personne clique.

        Arbitré le 2026-11-03 : l'avis de changement automatique suit la
        résolution commune, les trois autres restent relus par une personne.
        """
        robot = self.env.ref("base.user_root")
        robot.sudo().signature = "<span>-- <br>System</span>"
        ev = self._rencontre(self.armee, partner_ids=[(6, 0, (
            self.invite + self.signataire.partner_id).ids)])
        modele = self.env.ref("bf_calendar_invite.mail_template_calendar_change")
        rendu = modele._render_field("body_html", ev.ids)[ev.id]
        self.assertNotIn("System", rendu)
        self.assertIn("Fabienne Essai", rendu)

    def test_l_entete_et_la_signature_nomment_la_meme_personne(self):
        """Le défaut de fond : une adresse d'un côté, une autre signature en bas."""
        ev = self._rencontre(self.armee)
        partner = ev._bf_confirm_sender_partner()
        self.assertEqual(partner, self.signataire.partner_id)
        self.assertIn(partner.email, ev._bf_confirm_sender() or "")
        self.assertIn(partner.name, ev.bf_confirm_signature() or ev.bf_confirm_signer_name())

@tagged("post_install", "-at_install")
class TestConfirmReminderMenu(TransactionCase):
    """L'armement doit avoir une commande visible.

    🔴 Le coeur réserve « Types de réunion » et le menu Configuration du
    calendrier à `base.group_no_one`, donc au mode développeur. La case qui
    arme le rappel vivait sur un écran que la personne censée s'en servir ne
    pouvait pas ouvrir.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.parametres = cls.env["res.users"].create({
            "name": "Paramètres Essai",
            "login": "parametres.essai@example.org",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id,
                                  cls.env.ref("base.group_system").id])],
        })
        cls.interne = cls.env["res.users"].create({
            "name": "Interne Essai",
            "login": "interne.menu.essai@example.org",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
        })

    def test_un_usager_parametres_voit_l_entree(self):
        menu = self.env.ref("bf_calendar_invite.menu_calendar_event_type_bf")
        self.assertTrue(
            menu.with_user(self.parametres)._filter_visible_menus(),
            "l'entrée doit être visible sans mode développeur",
        )

    def test_contre_epreuve_celle_du_coeur_reste_cachee(self):
        """Sans cette contre-épreuve, l'essai ci-dessus pourrait passer parce
        que TOUS les menus sont visibles, et ne rien prouver."""
        coeur = self.env.ref("calendar.menu_calendar_event_type")
        self.assertFalse(
            coeur.with_user(self.parametres)._filter_visible_menus(),
            "celle du coeur est réservée au mode développeur",
        )

    def test_un_interne_ordinaire_ne_la_voit_pas(self):
        """`calendar.event.type` est en lecture seule pour `base.group_user` :
        l'écran n'est offert qu'à qui peut l'écrire."""
        menu = self.env.ref("bf_calendar_invite.menu_calendar_event_type_bf")
        self.assertFalse(menu.with_user(self.interne)._filter_visible_menus())

    def test_l_entree_ouvre_bien_la_liste_des_etiquettes(self):
        menu = self.env.ref("bf_calendar_invite.menu_calendar_event_type_bf")
        action = self.env.ref("calendar.action_calendar_event_type")
        # ⚠️ `ir.ui.menu.action` est une référence : Odoo 18 la rend en
        # ENREGISTREMENT, pas en chaîne « modèle,id ».
        self.assertEqual(menu.action, action)
        self.assertEqual(action.res_model, "calendar.event.type")
