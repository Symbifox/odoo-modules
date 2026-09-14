# -*- coding: utf-8 -*-
"""Les essais du pont entre le statut et le répondeur.

Ce qui est éprouvé ici sort de mesures réelles et non d'une couverture de
lignes : le fuseau qui décale la période d'un jour, la relève qui
laisse une parenthèse vide dans un courriel parti chez un client, la récurrence
d'un férié qui court jusqu'en 2744, et l'organisateur qui n'est plus celui qui
s'absente.
"""

import re
from datetime import date, datetime, timedelta

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "bf_absence_autoreply")
class TestAbsenceAutoreply(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Absence = cls.env["bf.partner.absence"]
        cls.Repondeur = cls.env["bf.email.absence"]
        cls.Maison = cls.env["bf.absence.house.message"]

        cls.olivia = cls.env["res.users"].create({
            "name": "Olivia Aotearoa",
            "login": "olivia.aotearoa@essai.test",
            "email": "olivia.aotearoa@essai.test",
            "tz": "Pacific/Auckland",
        })
        cls.thomas = cls.env["res.users"].create({
            "name": "Thomas Toronto",
            "login": "thomas.toronto@essai.test",
            "email": "thomas.toronto@essai.test",
            "tz": "America/Toronto",
        })
        cls.client_externe = cls.env["res.partner"].create({
            "name": "Cliente Externe",
            "email": "cliente@ailleurs.test",
        })

    def _absence(self, partner, debut, fin, **kw):
        vals = {"partner_id": partner.id, "date_from": debut, "date_to": fin,
                "reminder": False}
        vals.update(kw)
        return self.Absence.create(vals)

    # ------------------------------------------------------------------
    # Qui peut armer
    # ------------------------------------------------------------------
    def test_une_absence_de_contact_narme_rien(self):
        """L'absence d'un client est une connaissance, pas un engagement."""
        absence = self._absence(self.client_externe, date(2026, 10, 5),
                                date(2026, 10, 9), autoreply=True)
        self.assertFalse(absence.absent_user_id)
        self.assertFalse(absence.email_absence_id)

    def test_le_statut_arme_le_repondeur_dune_personne_de_la_maison(self):
        absence = self._absence(self.thomas.partner_id, date(2026, 10, 5),
                                date(2026, 10, 9), autoreply=True)
        self.assertEqual(absence.absent_user_id, self.thomas)
        repondeur = absence.email_absence_id
        self.assertTrue(repondeur)
        self.assertEqual(repondeur.user_id, self.thomas)
        self.assertTrue(repondeur.active)
        self.assertEqual(len(repondeur.reply_ids), 1)
        self.assertIn("{retour}", repondeur.reply_ids.body_html)

    def test_sans_la_case_rien_ne_sarme(self):
        absence = self._absence(self.thomas.partner_id, date(2026, 10, 5),
                                date(2026, 10, 9))
        self.assertFalse(absence.email_absence_id)

    # ------------------------------------------------------------------
    # 🔴 Qui a le droit d'armer
    # ------------------------------------------------------------------
    def test_un_employe_arme_son_propre_repondeur(self):
        employe = self.env["res.users"].create({
            "name": "Employée Ordinaire",
            "login": "ordinaire@essai.test",
            "email": "ordinaire@essai.test",
            "tz": "America/Toronto",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])],
        })
        absence = self.Absence.with_user(employe).create({
            "partner_id": employe.partner_id.id,
            "date_from": date(2026, 10, 5),
            "date_to": date(2026, 10, 9),
            "reminder": False,
            "autoreply": True,
        })
        self.assertTrue(absence.email_absence_id)

    def test_un_employe_narme_pas_le_repondeur_dun_collegue(self):
        """Un message automatique part sous le nom de l'autre, de sa boîte."""
        employe = self.env["res.users"].create({
            "name": "Employé Ordinaire Deux",
            "login": "ordinaire2@essai.test",
            "email": "ordinaire2@essai.test",
            "tz": "America/Toronto",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])],
        })
        with self.assertRaises(UserError):
            self.Absence.with_user(employe).create({
                "partner_id": self.thomas.partner_id.id,
                "date_from": date(2026, 10, 5),
                "date_to": date(2026, 10, 9),
                "reminder": False,
                "autoreply": True,
            })

    def test_un_employe_peut_noter_labsence_dun_collegue_sans_armer(self):
        employe = self.env["res.users"].create({
            "name": "Employé Ordinaire Trois",
            "login": "ordinaire3@essai.test",
            "email": "ordinaire3@essai.test",
            "tz": "America/Toronto",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])],
        })
        absence = self.Absence.with_user(employe).create({
            "partner_id": self.thomas.partner_id.id,
            "date_from": date(2026, 10, 5),
            "date_to": date(2026, 10, 9),
            "reminder": False,
        })
        self.assertTrue(absence)
        self.assertFalse(absence.email_absence_id)

    # ------------------------------------------------------------------
    # 🔴 Le fuseau
    # ------------------------------------------------------------------
    def test_la_periode_se_lit_dans_le_fuseau_de_la_personne_absente(self):
        """« Du 5 au 9 » ne commence pas au même instant des deux côtés.

        Seize heures séparent Auckland de Toronto : une période lue dans le
        mauvais fuseau allume ou éteint le répondeur presque un jour à côté.
        """
        nz = self._absence(self.olivia.partner_id, date(2026, 10, 5),
                           date(2026, 10, 9), autoreply=True)
        qc = self._absence(self.thomas.partner_id, date(2026, 10, 5),
                           date(2026, 10, 9), autoreply=True)
        debut_nz = nz.email_absence_id.date_from
        debut_qc = qc.email_absence_id.date_from
        self.assertNotEqual(debut_nz, debut_qc)
        # Le 5 octobre 2026 à 00:00 à Auckland (UTC+13 en heure d'été) vaut le
        # 4 octobre à 11:00 UTC.
        self.assertEqual(debut_nz, datetime(2026, 10, 4, 11, 0, 0))
        # Le même instant local à Toronto (UTC-4) vaut le 5 à 04:00 UTC.
        self.assertEqual(debut_qc, datetime(2026, 10, 5, 4, 0, 0))
        # Et la fin couvre bien le DERNIER jour en entier.
        self.assertEqual(nz.email_absence_id.date_to,
                         datetime(2026, 10, 9, 10, 59, 59))

    def test_un_fuseau_illisible_ne_fait_pas_tomber_larmement(self):
        """Le champ refuse un fuseau inventé, la BASE peut en porter un.

        ⚠️ `res.users.tz` est une sélection : `write` refuse « Mars/Olympus »
        et la garde paraîtrait inatteignable. Elle ne l'est pas : une base
        restaurée d'ailleurs, ou une colonne écrite hors ORM, porte ce que
        personne n'aurait pu taper. L'essai écrit donc en SQL.
        """
        self.env.cr.execute(
            "UPDATE res_partner SET tz = %s WHERE id = %s",
            ("Mars/Olympus", self.thomas.partner_id.id))
        self.env.invalidate_all()
        self.assertEqual(self.thomas.tz, "Mars/Olympus")
        absence = self._absence(self.thomas.partner_id, date(2026, 10, 5),
                                date(2026, 10, 9), autoreply=True)
        self.assertTrue(absence.email_absence_id)
        # Repli sur UTC : la journée entière, pas une exception.
        self.assertEqual(absence.email_absence_id.date_from,
                         datetime(2026, 10, 5, 0, 0, 0))

    # ------------------------------------------------------------------
    # La relève
    # ------------------------------------------------------------------
    def test_sans_releve_la_phrase_de_releve_nest_pas_ecrite(self):
        """🔴 Le marqueur vide laissait « écrivez à  () » chez un client."""
        absence = self._absence(self.thomas.partner_id, date(2026, 10, 5),
                                date(2026, 10, 9), autoreply=True)
        corps = absence.email_absence_id.reply_ids.body_html
        self.assertNotIn("écrivez à", corps)
        self.assertNotIn("()", corps)

    def test_une_releve_fichee_est_nommee_en_clair(self):
        absence = self._absence(
            self.thomas.partner_id, date(2026, 10, 5), date(2026, 10, 9),
            autoreply=True, backup_partner_id=self.olivia.partner_id.id)
        corps = absence.email_absence_id.reply_ids.body_html
        self.assertIn("Olivia Aotearoa", corps)
        self.assertIn("olivia.aotearoa@essai.test", corps)
        self.assertNotIn("[[releve]]", corps)

    def test_une_releve_en_texte_libre_ne_laisse_pas_de_parenthese_vide(self):
        absence = self._absence(
            self.thomas.partner_id, date(2026, 10, 5), date(2026, 10, 9),
            autoreply=True, backup_info="la réception, 450 555-1212")
        corps = absence.email_absence_id.reply_ids.body_html
        self.assertIn("la réception", corps)
        self.assertNotIn("()", corps)

    def test_la_releve_est_nommee_mais_ne_recoit_pas_la_boite(self):
        """Confier le courrier de quelqu'un est un geste explicite."""
        absence = self._absence(
            self.thomas.partner_id, date(2026, 10, 5), date(2026, 10, 9),
            autoreply=True, backup_partner_id=self.olivia.partner_id.id)
        self.assertEqual(absence.email_absence_id.delegate_user_id, self.olivia)
        self.assertFalse(absence.email_absence_id.route_to_delegate)

    # ------------------------------------------------------------------
    # Le texte
    # ------------------------------------------------------------------
    def test_le_gabarit_personnel_lemporte_sur_le_message_de_maison(self):
        self.Repondeur.create({
            "name": "Mon message type",
            "user_id": self.thomas.id,
            "is_template": True,
            "reply_ids": [(0, 0, {"name": "Tout le monde",
                                  "body_html": "<p>Mon texte à moi.</p>"})],
        })
        absence = self._absence(self.thomas.partner_id, date(2026, 10, 5),
                                date(2026, 10, 9), autoreply=True)
        self.assertIn("Mon texte à moi",
                      absence.email_absence_id.reply_ids.body_html)

    def test_sans_texte_on_ne_repond_pas(self):
        """Un gabarit vide chez un client coûte plus cher qu'un silence."""
        self.Maison.sudo().search([]).write({"active": False})
        with self.assertRaises(UserError):
            self._absence(self.thomas.partner_id, date(2026, 10, 5),
                          date(2026, 10, 9), autoreply=True)

    def test_le_ton_choisit_le_texte(self):
        delai = self._absence(self.thomas.partner_id, date(2026, 10, 5),
                              date(2026, 10, 9), autoreply=True,
                              autoreply_tone="delay")
        dehors = self._absence(self.olivia.partner_id, date(2026, 10, 5),
                               date(2026, 10, 9), autoreply=True,
                               autoreply_tone="away")
        self.assertNotEqual(delai.email_absence_id.reply_ids.body_html,
                            dehors.email_absence_id.reply_ids.body_html)
        self.assertIn("plus lentes",
                      delai.email_absence_id.reply_ids.body_html)
        self.assertIn("ne consulte pas",
                      dehors.email_absence_id.reply_ids.body_html)

    def test_aucun_texte_de_maison_ne_saccorde_en_genre(self):
        """Odoo ne porte pas le genre d'une personne, et le message part sous
        le nom de n'importe qui."""
        for message in self.Maison.sudo().search([]):
            for mot in ("absent ", "absente", "parti ", "partie "):
                self.assertNotIn(mot, (message.body_html or "").lower())

    # ------------------------------------------------------------------
    # Ce qui éteint
    # ------------------------------------------------------------------
    def test_decocher_la_case_eteint_le_repondeur(self):
        absence = self._absence(self.thomas.partner_id, date(2026, 10, 5),
                                date(2026, 10, 9), autoreply=True)
        repondeur = absence.email_absence_id
        absence.autoreply = False
        self.assertFalse(repondeur.active)

    def test_archiver_labsence_eteint_le_repondeur(self):
        absence = self._absence(self.thomas.partner_id, date(2026, 10, 5),
                                date(2026, 10, 9), autoreply=True)
        repondeur = absence.email_absence_id
        absence.active = False
        self.assertFalse(repondeur.active)

    def test_supprimer_labsence_eteint_sans_effacer_le_journal(self):
        absence = self._absence(self.thomas.partner_id, date(2026, 10, 5),
                                date(2026, 10, 9), autoreply=True)
        repondeur = absence.email_absence_id
        absence.unlink()
        self.assertTrue(repondeur.exists())
        self.assertFalse(repondeur.active)

    def test_rentrer_plus_tot_raccourcit_le_repondeur(self):
        absence = self._absence(self.thomas.partner_id, date(2026, 10, 5),
                                date(2026, 10, 9), autoreply=True)
        avant = absence.email_absence_id.date_to
        absence.date_to = date(2026, 10, 6)
        self.assertLess(absence.email_absence_id.date_to, avant)

    def test_deux_repondeurs_sur_les_memes_dates_sont_refuses(self):
        self.Repondeur.create({
            "name": "Déjà armé à la main",
            "user_id": self.thomas.id,
            "date_from": datetime(2026, 10, 6, 0, 0, 0),
            "date_to": datetime(2026, 10, 8, 0, 0, 0),
            "reply_ids": [(0, 0, {"name": "Tout le monde",
                                  "body_html": "<p>Déjà là.</p>"})],
        })
        with self.assertRaises(UserError):
            self._absence(self.thomas.partner_id, date(2026, 10, 5),
                          date(2026, 10, 9), autoreply=True)

    # ------------------------------------------------------------------
    # 🔴 La détection à l'agenda
    # ------------------------------------------------------------------
    def _evenement(self, nom, debut, fin, organisateur, participants=None,
                   **kw):
        vals = {
            "name": nom,
            "start": debut,
            "stop": fin,
            "allday": True,
            "user_id": organisateur.id,
            "partner_ids": [(6, 0, [p.id for p in (participants or [])])],
        }
        vals.update(kw)
        return self.env["calendar.event"].create(vals)

    def test_un_evenement_organise_par_quelquun_dautre_compte_quand_meme(self):
        """🔴 643 des 970 événements de 2026 sont organisés par __system__."""
        self.thomas.sudo().write({"bf_absence_from_calendar": True})
        evenement = self._evenement(
            "Vacances", datetime(2026, 11, 2), datetime(2026, 11, 6),
            organisateur=self.olivia, participants=[self.thomas.partner_id])
        motif = re.compile(self.Repondeur._calendar_pattern())
        self.assertTrue(
            self.Repondeur._calendar_event_arms(self.thomas, evenement, motif))

    def test_une_invitation_declinee_ne_dit_pas_que_je_suis_absent(self):
        evenement = self._evenement(
            "Vacances", datetime(2026, 11, 2), datetime(2026, 11, 6),
            organisateur=self.olivia, participants=[self.thomas.partner_id])
        evenement.attendee_ids.filtered(
            lambda a: a.partner_id == self.thomas.partner_id
        ).write({"state": "declined"})
        motif = re.compile(self.Repondeur._calendar_pattern())
        self.assertFalse(
            self.Repondeur._calendar_event_arms(self.thomas, evenement, motif))

    def test_une_occurrence_de_recurrence_narme_rien(self):
        """🔴 « St-Jean-Baptiste (férié) » : 720 occurrences jusqu'en 2744."""
        evenement = self._evenement(
            "St-Jean-Baptiste (férié)", datetime(2026, 6, 24),
            datetime(2026, 6, 24), organisateur=self.thomas,
            participants=[self.thomas.partner_id],
            recurrency=True, rrule_type="yearly", count=720,
            event_tz="America/Toronto")
        motif = re.compile(self.Repondeur._calendar_pattern())
        self.assertFalse(
            self.Repondeur._calendar_event_arms(self.thomas, evenement, motif))

    def test_un_evenement_trop_long_narme_rien(self):
        evenement = self._evenement(
            "Congé sabbatique", datetime(2026, 1, 1), datetime(2026, 12, 31),
            organisateur=self.thomas, participants=[self.thomas.partner_id])
        motif = re.compile(self.Repondeur._calendar_pattern())
        self.assertFalse(
            self.Repondeur._calendar_event_arms(self.thomas, evenement, motif))

    def test_le_cron_trouve_une_absence_organisee_par_un_autre(self):
        self.thomas.sudo().write({"bf_absence_from_calendar": True})
        debut = datetime.now() + timedelta(days=10)
        self._evenement(
            "Vacances d'automne", debut, debut + timedelta(days=4),
            organisateur=self.olivia, participants=[self.thomas.partner_id])
        self.Repondeur._cron_sync_calendar()
        cree = self.Repondeur.sudo().search([
            ("user_id", "=", self.thomas.id), ("source", "=", "calendar")])
        self.assertEqual(len(cree), 1)
        self.assertTrue(cree.reply_ids)

    def test_le_message_de_maison_supplee_le_gabarit_absent(self):
        """C'est ce préalable qui expliquait le zéro partout où il tournait."""
        self.assertIsNotNone(self.Repondeur._absence_seed(self.thomas))

    def test_sans_message_nulle_part_le_cron_ne_cree_rien(self):
        self.Maison.sudo().search([]).write({"active": False})
        self.thomas.sudo().write({"bf_absence_from_calendar": True})
        debut = datetime.now() + timedelta(days=10)
        self._evenement(
            "Vacances d'automne", debut, debut + timedelta(days=4),
            organisateur=self.thomas, participants=[self.thomas.partner_id])
        self.Repondeur._cron_sync_calendar()
        self.assertFalse(self.Repondeur.sudo().search([
            ("user_id", "=", self.thomas.id), ("source", "=", "calendar")]))

    # ------------------------------------------------------------------
    # Ce que le correspondant lit
    # ------------------------------------------------------------------
    def test_la_date_de_retour_se_lit_dans_le_fuseau_de_labsent(self):
        """🔴 Sinon une absence finie le 9 s'annonce « 10 » à l'autre bout."""
        absence = self._absence(self.thomas.partner_id, date(2026, 10, 5),
                                date(2026, 10, 9), autoreply=True)
        repondeur = absence.email_absence_id
        marqueurs = repondeur.with_user(self.olivia)._placeholders(
            self.env["bf.email"], lang="fr_CA")
        self.assertIn("9", marqueurs["{retour}"])
        self.assertIn("octobre", marqueurs["{retour}"])

    def test_la_date_de_retour_nest_pas_au_format_court_ambigu(self):
        absence = self._absence(self.thomas.partner_id, date(2026, 10, 5),
                                date(2026, 10, 9), autoreply=True)
        marqueurs = absence.email_absence_id._placeholders(
            self.env["bf.email"], lang="fr_CA")
        self.assertNotIn("/", marqueurs["{retour}"])

    # ------------------------------------------------------------------
    # L'assistant
    # ------------------------------------------------------------------
    def test_lassistant_pose_labsence_et_arme_dun_coup(self):
        wizard = self.env["bf.absence.me.wizard"].with_user(self.thomas).create({
            "date_from": date(2026, 10, 5),
            "date_to": date(2026, 10, 9),
        })
        wizard.action_confirm()
        absence = self.Absence.search([
            ("partner_id", "=", self.thomas.partner_id.id)])
        self.assertEqual(len(absence), 1)
        self.assertTrue(absence.email_absence_id)
        self.assertTrue(absence.autoreply_decline_meetings)
        # ⚠️ Aucun rappel « prendre des nouvelles » sur sa propre fiche.
        self.assertFalse(absence.reminder)
        self.assertFalse(absence.reminder_activity_id)

    def test_lassistant_montre_le_texte_avant_de_larmer(self):
        wizard = self.env["bf.absence.me.wizard"].with_user(self.thomas).create({
            "date_from": date(2026, 10, 5),
            "date_to": date(2026, 10, 9),
        })
        self.assertIn("{retour}", wizard.preview_html or "")
