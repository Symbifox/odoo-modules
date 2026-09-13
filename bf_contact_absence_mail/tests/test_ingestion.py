# -*- coding: utf-8 -*-
"""Les essais de la lecture des répondeurs.

⚠️ Les textes employés ici sont des répondeurs RÉELS reçus entre
2025-07 et 2026-09, recopiés tels quels (espace insécable comprise, et « aout »
sans accent comme l'expéditeur l'a écrit). Un jeu d'essai inventé aurait été
plus propre et aurait raté les trois pièges qui comptent.
"""

import base64
from datetime import date
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "bf_absence")
class TestLectureRepondeurs(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Email = cls.env["bf.email"]
        cls.Suggestion = cls.env["bf.partner.absence.suggestion"]
        cls.contact = cls.env["res.partner"].create({
            "name": "Mathieu Essai",
            "email": "mathieu@exemple-essai.test",
        })
        cls.releve = cls.env["res.partner"].create({
            "name": "Support Essai",
            "email": "support@exemple-essai.test",
        })

    def _courriel(self, sujet="", corps="", entetes="", partenaire=None,
                  recu="2026-08-10 12:00:00", de=None, **kw):
        """Un `bf.email` construit comme la production le construit.

        ⚠️ `body_preview` et `body_html` sont CALCULÉS : les écrire à la
        création ne fait rien, et un jeu d'essai qui les pose se retrouve avec
        un corps vide sans le dire. La copie brute RFC 822 est la vraie source,
        et la poser ici fait passer les essais par le même chemin que le
        courrier réel.
        """
        de = de or '"Mathieu" <mathieu@exemple-essai.test>'
        brut = (
            "From: %s\r\n"
            "To: destinataire@exemple.test\r\n"
            "Subject: %s\r\n"
            "%s"
            "Content-Type: text/plain; charset=utf-8\r\n"
            "\r\n"
            "%s\r\n"
        ) % (de, sujet, (entetes + "\r\n") if entetes else "", corps)
        vals = {
            "date": recu,
            "direction": "in",
            "source": "imap",
            "subject": sujet,
            "raw_rfc822": base64.b64encode(brut.encode("utf-8")),
            "raw_headers": entetes,
            "email_from": de,
            "partner_id": (partenaire or self.contact).id,
        }
        vals.update(kw)
        return self.Email.create(vals)

    # ------------------------------------------------------------------
    # Reconnaissance
    # ------------------------------------------------------------------
    def test_objet_outlook_francais_avec_espace_insecable(self):
        """🔴 Outlook francophone écrit « Réponse automatique\xa0: ».

        Un filtre en espace ordinaire ne verrait rien, et c'est seize des
        vingt-huit répondeurs mesurés qui passeraient au travers.
        """
        courriel = self._courriel(
            sujet="Réponse automatique\xa0: Tableaux de bord",
            corps="Veuillez noter que je suis en vacances jusqu'au 15 août.")
        reconnu, par_quoi = courriel._bf_absence_signals()
        self.assertTrue(reconnu)
        self.assertEqual(par_quoi, "objet")

    def test_entete_rfc_3834_sans_objet_parlant(self):
        courriel = self._courriel(
            sujet="Re: Suivi",
            corps="Je serai absent.",
            entetes="Auto-Submitted: auto-replied\r\nX-Mailer: Exchange")
        reconnu, par_quoi = courriel._bf_absence_signals()
        self.assertTrue(reconnu)
        self.assertEqual(par_quoi, "en-tête Auto-Submitted")

    def test_auto_generated_n_est_pas_un_repondeur(self):
        """🔴 La régression qui a rendu 1 510 faux positifs sur 1 531.

        La RFC 3834 §5 distingue `auto-replied` (une réponse personnelle
        produite par une machine) de `auto-generated` (une notification). Nos
        rapports de sauvegarde, nos confirmations de rendez-vous et les avis
        Nextcloud portent tous le second.
        """
        courriel = self._courriel(
            sujet="[OK] Rapport de sauvegarde - BKP-00112",
            corps="Toutes les sauvegardes ont réussi.",
            entetes="Auto-Submitted: auto-generated")
        self.assertFalse(courriel._bf_absence_signals()[0])

    def test_x_auto_response_suppress_seul_ne_compte_pas(self):
        """C'est une consigne pour que l'AUTRE ne réponde pas automatiquement,
        pas une déclaration que ce message-ci est un répondeur."""
        courriel = self._courriel(
            sujet="Confirmation de votre rendez-vous",
            corps="Votre rendez-vous est confirmé.",
            entetes="X-Auto-Response-Suppress: OOF, DR, RN, NRN")
        self.assertFalse(courriel._bf_absence_signals()[0])

    def test_auto_submitted_no_ne_compte_pas(self):
        courriel = self._courriel(
            sujet="Re: Suivi", corps="Bonjour",
            entetes="Auto-Submitted: no")
        reconnu, _par_quoi = courriel._bf_absence_signals()
        self.assertFalse(reconnu)

    def test_absence_seule_dans_l_objet_exige_un_indice_du_corps(self):
        """« ABSENCE Re: Suivi » est un vrai répondeur, « Absence de Camille »
        n'en est pas un. L'objet seul ne tranche pas."""
        vrai = self._courriel(
            sujet="ABSENCE Re: Suivi",
            corps="Veuillez prendre note que je serai en vacances.")
        faux = self._courriel(
            sujet="Absence de Camille à la rencontre",
            corps="Elle ne pourra pas se joindre à nous mardi.")
        self.assertTrue(vrai._bf_absence_signals()[0])
        self.assertFalse(faux._bf_absence_signals()[0])

    def test_un_courriel_ordinaire_n_est_pas_un_repondeur(self):
        courriel = self._courriel(
            sujet="Facture 2026-0042",
            corps="Bonjour, voici la facture du mois.")
        self.assertFalse(courriel._bf_absence_signals()[0])

    # ------------------------------------------------------------------
    # Exclusions
    # ------------------------------------------------------------------
    def test_un_mailer_daemon_est_ecarte(self):
        """🔴 Mesuré : mailer-daemon@googlemail.com est entré deux fois dans
        le filet. Sans exclusion, une ingestion silencieuse aurait mis un
        MAILER-DAEMON en vacances."""
        courriel = self._courriel(
            sujet="Réponse automatique : échec de remise",
            corps="de retour le 17 août",
            de="Mail Delivery Subsystem <mailer-daemon@googlemail.com>")
        self.assertEqual(courriel._bf_absence_excluded(), "adresse de machine")

    def test_un_collegue_interne_est_ecarte(self):
        utilisateur = self.env["res.users"].create({
            "name": "Collegue Ingestion",
            "login": "collegue.ingestion.test",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])],
        })
        courriel = self._courriel(
            sujet="Réponse automatique : vacances",
            corps="de retour le 17 août",
            partenaire=utilisateur.partner_id)
        self.assertEqual(courriel._bf_absence_excluded(), "contact interne")

    def test_un_courriel_de_nos_propres_domaines_est_ecarte(self):
        """🔴 Nos machines écrivent beaucoup, et certaines de leurs fiches
        partenaires ne portent aucun utilisateur : le test « contact interne »
        ne les attrapait pas. C'est le domaine qui tranche."""
        societe = self.env.company
        societe.email = "bonjour@maison-essai.test"
        maison = self.env["res.partner"].create({
            "name": "Robot Maison", "email": "avis@maison-essai.test"})
        courriel = self._courriel(
            sujet="Réponse automatique : avis",
            corps="de retour le 17 août",
            de="Avis <avis@maison-essai.test>",
            partenaire=maison)
        self.assertEqual(courriel._bf_absence_excluded(), "domaine de la maison")

    def test_un_envoi_en_masse_est_ecarte(self):
        """`is_bulk` est CALCULÉ depuis List-Unsubscribe : l'essai pose
        l'en-tête plutôt que le drapeau, sinon il n'éprouve rien."""
        courriel = self._courriel(
            sujet="Réponse automatique : infolettre",
            corps="de retour le 17 août",
            entetes="List-Unsubscribe: <mailto:stop@infolettre.test>")
        self.assertTrue(courriel.is_bulk)
        self.assertEqual(courriel._bf_absence_excluded(), "envoi en masse")

    def test_un_courriel_sans_contact_fiche_est_ecarte(self):
        courriel = self.Email.create({
            "date": "2026-08-10 12:00:00",
            "direction": "in",
            "source": "gateway",
            "subject": "Réponse automatique : vacances",
            "email_from": "inconnu@nulle-part.test",
        })
        self.assertEqual(courriel._bf_absence_excluded(), "sans contact fiché")

    # ------------------------------------------------------------------
    # Lecture des dates, sur la prose réelle
    # ------------------------------------------------------------------
    def test_periode_explicite_avec_annee(self):
        courriel = self._courriel(
            sujet="ABSENCE Re: Suivi",
            corps="Bonjour, Veuillez prendre note que je serai en vacances "
                  "pendant la période du 21 août 2026 au 15 septembre 2026, "
                  "inclusivement.",
            recu="2026-09-10 00:01:19")
        debut, fin, _releve = courriel._bf_absence_read_period()
        self.assertEqual(debut, date(2026, 8, 21))
        self.assertEqual(fin, date(2026, 9, 15))

    def test_de_retour_le_veut_dire_absent_la_veille(self):
        """🔴 L'erreur d'un jour qui ferait parler le bandeau le matin même
        du retour."""
        courriel = self._courriel(
            sujet=" Réponse automatique\xa0: HT00032",
            corps="Veuillez prendre note que je serai en vacances et de retour "
                  "le 17 août. Pour toute demande urgente, je vous invite à "
                  "contacter le support à support@exemple-essai.test",
            recu="2026-08-10 18:14:56")
        debut, fin, releve = courriel._bf_absence_read_period()
        self.assertEqual(fin, date(2026, 8, 16))
        self.assertEqual(releve, "support@exemple-essai.test")
        self.assertLessEqual(debut, fin)

    def test_jusqu_au_puis_retour_au_bureau_le_lendemain(self):
        courriel = self._courriel(
            sujet="Réponse automatique : Re: Question",
            corps="Je suis en vacances jusqu'au 3 mai. Je répondrai à votre "
                  "courriel à mon retour au bureau le lundi 4 mai.",
            recu="2026-04-16 14:08:43")
        _debut, fin, _releve = courriel._bf_absence_read_period()
        self.assertEqual(fin, date(2026, 5, 3))

    def test_les_deux_bornes_sans_accent_ni_annee(self):
        courriel = self._courriel(
            sujet=" Réponse automatique\xa0: Brouillon",
            corps="Veuillez prendre note que je serai absent du bureau du "
                  "jeudi 13 aout au vendredi 21 aout inclus.",
            recu="2026-08-13 21:55:19")
        debut, fin, _releve = courriel._bf_absence_read_period()
        self.assertEqual(debut, date(2026, 8, 13))
        self.assertEqual(fin, date(2026, 8, 21))

    def test_du_20_au_31_juillet_garde_les_deux_bornes(self):
        """🔴 Le premier jour n'a pas son mois. Sans motif d'intervalle, une
        fermeture de douze jours se réduirait au 31 juillet."""
        courriel = self._courriel(
            sujet="Réponse automatique : Demande",
            corps="Veuillez noter que nous serons fermés pour les vacances de "
                  "la construction, du 20 au 31 juillet inclusivement.",
            recu="2026-07-20 07:14:03")
        debut, fin, _releve = courriel._bf_absence_read_period()
        self.assertEqual(debut, date(2026, 7, 20))
        self.assertEqual(fin, date(2026, 7, 31))

    def test_prose_anglaise_avec_ordinal(self):
        courriel = self._courriel(
            sujet="Automatic reply: Following up",
            corps="Hi there, I am on annual leave, returning Monday 17th "
                  "August. I won't be monitoring my emails.",
            recu="2026-07-21 23:04:32")
        _debut, fin, _releve = courriel._bf_absence_read_period()
        self.assertEqual(fin, date(2026, 8, 16))

    def test_le_corps_n_est_lu_qu_une_fois(self):
        """🔴 L'aperçu est le préfixe du corps : les concaténer faisait
        apparaître chaque date en double et rendait inatteignable la branche
        « une seule date lue ». Trouvé par mutation, pas par relecture."""
        courriel = self._courriel(
            sujet="Réponse automatique : Suivi",
            corps="Je serai de retour le 17 août.")
        texte = courriel._bf_absence_text()
        self.assertEqual(texte.count("17 août"), 1, texte)
        ancre = date(2026, 8, 10)
        self.assertEqual(
            len(courriel._bf_absence_dates_from_text(texte, ancre)), 1)

    def test_un_repondeur_sans_date_ne_rend_rien(self):
        """Neuf des vingt-huit répondeurs mesurés sont dans ce cas."""
        courriel = self._courriel(
            sujet="Réponse automatique : Re: Suivi",
            corps="Bonjour, je suis actuellement absent du bureau et je "
                  "prendrai connaissance de votre message à mon retour.")
        debut, fin, _releve = courriel._bf_absence_read_period()
        self.assertFalse(debut)
        self.assertFalse(fin)

    def test_la_releve_ne_peut_pas_etre_l_expediteur_lui_meme(self):
        courriel = self._courriel(
            sujet="Réponse automatique : Re: Suivi",
            corps="Je suis absent. Écrivez-moi à mathieu@exemple-essai.test.")
        _debut, _fin, releve = courriel._bf_absence_read_period()
        self.assertFalse(releve)

    # ------------------------------------------------------------------
    # La passe, sans Gen
    # ------------------------------------------------------------------
    def test_la_passe_fonctionne_sans_gen(self):
        """⚠️ Exigence explicite : Gen absent n'empêche ni l'installation ni
        le fonctionnement. Le pont est muet, la suggestion est posée quand
        même, avec les dates lues en clair."""
        courriel = self._courriel(
            sujet="Réponse automatique\xa0: Suivi",
            corps="Je serai en vacances du 13 aout au 21 aout inclus.",
            recu="2026-08-12 09:00:00")
        with patch.object(type(self.env["bf.ai.bridge"]), "available",
                          return_value=False):
            poses = courriel._bf_absence_process()
        self.assertEqual(poses, 1)
        suggestion = courriel.bf_absence_suggestion_id
        self.assertEqual(suggestion.date_from, date(2026, 8, 13))
        self.assertEqual(suggestion.date_to, date(2026, 8, 21))
        self.assertEqual(suggestion.read_by, "règles")
        self.assertTrue(suggestion.complete)
        self.assertTrue(courriel.bf_absence_scanned)

    def test_la_passe_marque_meme_ce_qu_elle_ecarte(self):
        """Sinon la passe planifiée relit le même courrier indéfiniment."""
        courriel = self._courriel(sujet="Facture", corps="Bonjour")
        with patch.object(type(self.env["bf.ai.bridge"]), "available",
                          return_value=False):
            poses = courriel._bf_absence_process()
        self.assertEqual(poses, 0)
        self.assertTrue(courriel.bf_absence_scanned)

    def test_un_meme_courriel_ne_propose_pas_deux_fois(self):
        courriel = self._courriel(
            sujet="Réponse automatique : Suivi",
            corps="de retour le 17 août")
        with patch.object(type(self.env["bf.ai.bridge"]), "available",
                          return_value=False):
            courriel._bf_absence_process()
            courriel.bf_absence_scanned = False
            with self.assertRaises(Exception):
                courriel._bf_absence_process()
                self.env.flush_all()

    # ------------------------------------------------------------------
    # Ce que Gen rend, et ce qu'on en garde
    # ------------------------------------------------------------------
    def test_gen_rend_une_ligne_json_utilisable(self):
        lu = self.Email._bf_absence_parse_gen(
            'Voici : {"debut": "2026-08-21", "fin": "2026-09-15", '
            '"releve": "jroy@exemple.test", "nature": "vacation"}')
        self.assertEqual(lu["debut"], date(2026, 8, 21))
        self.assertEqual(lu["fin"], date(2026, 9, 15))
        self.assertEqual(lu["releve"], "jroy@exemple.test")

    def test_une_date_mal_formee_de_gen_est_jetee(self):
        lu = self.Email._bf_absence_parse_gen(
            '{"debut": "le 21 août", "fin": "2026-09-15"}')
        self.assertNotIn("debut", lu)
        self.assertEqual(lu["fin"], date(2026, 9, 15))

    def test_une_reponse_qui_n_est_pas_du_json_ne_casse_rien(self):
        self.assertIsNone(self.Email._bf_absence_parse_gen(
            "Je ne sais pas quelles sont les dates."))
        self.assertIsNone(self.Email._bf_absence_parse_gen(""))

    def test_une_nature_inventee_par_gen_est_jetee(self):
        lu = self.Email._bf_absence_parse_gen(
            '{"fin": "2026-09-15", "nature": "maladie"}')
        self.assertNotIn("nature", lu)

    # ------------------------------------------------------------------
    # Accepter, refuser
    # ------------------------------------------------------------------
    def test_accepter_pose_l_absence_et_apparie_la_releve_par_l_adresse(self):
        suggestion = self.Suggestion.create({
            "partner_id": self.contact.id,
            "date_from": date(2026, 8, 13),
            "date_to": date(2026, 8, 21),
            "backup_hint": "support@exemple-essai.test",
        })
        self.assertEqual(suggestion.backup_partner_id, self.releve)
        suggestion.action_accept()
        self.assertEqual(suggestion.state, "accepted")
        absence = suggestion.absence_id
        self.assertTrue(absence)
        self.assertEqual(absence.source, "autoreply")
        self.assertEqual(absence.backup_partner_id, self.releve)
        self.assertEqual(absence.date_to, date(2026, 8, 21))

    def test_une_releve_sans_fiche_reste_du_texte(self):
        suggestion = self.Suggestion.create({
            "partner_id": self.contact.id,
            "date_from": date(2026, 8, 13),
            "date_to": date(2026, 8, 21),
            "backup_hint": "450 445-1750",
        })
        self.assertFalse(suggestion.backup_partner_id)
        suggestion.action_accept()
        self.assertEqual(suggestion.absence_id.backup_info, "450 445-1750")

    def test_une_proposition_sans_periode_ne_s_accepte_pas(self):
        suggestion = self.Suggestion.create({"partner_id": self.contact.id})
        self.assertFalse(suggestion.complete)
        with self.assertRaises(UserError):
            suggestion.action_accept()

    def test_refuser_ne_pose_rien(self):
        suggestion = self.Suggestion.create({
            "partner_id": self.contact.id,
            "date_from": date(2026, 8, 13),
            "date_to": date(2026, 8, 21),
        })
        suggestion.action_reject()
        self.assertEqual(suggestion.state, "rejected")
        self.assertFalse(suggestion.absence_id)


@tagged("post_install", "-at_install", "bf_absence")
class TestAccusesEtRattrapage(TransactionCase):
    """Les deux quick wins du pont : l'accusé de réception, et la passe de
    rattrapage qui fait naître le module plein."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Email = cls.env["bf.email"]
        cls.Suggestion = cls.env["bf.partner.absence.suggestion"]
        cls.contact = cls.env["res.partner"].create({
            "name": "Candidat Essai", "email": "rh@employeur-essai.test"})

    def _courriel(self, sujet, corps, recu="2026-07-12 09:00:00"):
        brut = (
            "From: RH <rh@employeur-essai.test>\r\n"
            "To: destinataire@essai-local.test\r\n"
            "Subject: %s\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n\r\n%s\r\n"
        ) % (sujet, corps)
        return self.Email.create({
            "date": recu, "direction": "in", "source": "imap",
            "subject": sujet, "raw_rfc822": base64.b64encode(brut.encode()),
            "email_from": "RH <rh@employeur-essai.test>",
            "partner_id": self.contact.id,
        })

    def test_un_accuse_de_candidature_sort_de_la_pile(self):
        """🔴 Trois des 25 reconnaissances du corpus réel sont des accusés de
        réception d'emploi : ils portent l'objet d'un répondeur sans en être
        un, et ils reviennent à chaque envoi."""
        courriel = self._courriel(
            "Automatic reply: Your Application",
            "Thank you. Your online application is currently being reviewed by "
            "our team. We will be in touch.")
        with patch.object(type(self.env["bf.ai.bridge"]), "available",
                          return_value=False):
            courriel._bf_absence_process()
        suggestion = courriel.bf_absence_suggestion_id
        self.assertTrue(suggestion)
        self.assertEqual(suggestion.kind, "acknowledgement")
        self.assertEqual(suggestion.state, "rejected")
        a_decider = self.Suggestion.search([("state", "=", "pending")])
        self.assertNotIn(suggestion, a_decider)

    def test_une_vraie_absence_qui_parle_de_candidature_reste_une_absence(self):
        """La période gagne sur l'indice : un message daté est une absence."""
        courriel = self._courriel(
            "Réponse automatique : Your Application",
            "Je suis en vacances jusqu'au 20 juillet. Votre candidature sera "
            "revue à mon retour.")
        with patch.object(type(self.env["bf.ai.bridge"]), "available",
                          return_value=False):
            courriel._bf_absence_process()
        suggestion = courriel.bf_absence_suggestion_id
        self.assertEqual(suggestion.kind, "absence")
        self.assertEqual(suggestion.state, "pending")
        self.assertEqual(suggestion.date_to, date(2026, 7, 20))

    def test_la_passe_de_rattrapage_releve_le_courrier_deja_la(self):
        self._courriel(
            "Réponse automatique : Suivi",
            "Je serai en vacances du 13 au 21 juillet inclusivement.")
        self._courriel("Facture 42", "Voici la facture du mois.")
        wizard = self.env["bf.absence.backfill.wizard"].create({
            "days": 4000, "limit": 100})
        self.assertGreaterEqual(wizard.pending, 2)
        with patch.object(type(self.env["bf.ai.bridge"]), "available",
                          return_value=False):
            action = wizard.action_run()
        self.assertEqual(action.get("res_model"),
                         "bf.partner.absence.suggestion")
        proposees = self.Suggestion.search(action["domain"])
        self.assertEqual(len(proposees), 1)
        self.assertEqual(proposees.date_to, date(2026, 7, 21))

    def test_la_passe_de_rattrapage_ne_dit_rien_quand_il_n_y_a_rien(self):
        self._courriel("Facture 43", "Voici la facture du mois.")
        wizard = self.env["bf.absence.backfill.wizard"].create({
            "days": 4000, "limit": 100})
        with patch.object(type(self.env["bf.ai.bridge"]), "available",
                          return_value=False):
            action = wizard.action_run()
        self.assertEqual(action.get("tag"), "display_notification")
