# -*- coding: utf-8 -*-
"""Les essais du socle d'absences.

Ce qui est éprouvé ici a été choisi sur les pièges relevés en instruisant le
l'usage réel, pas sur la couverture de lignes : l'erreur d'un jour entre
« jusqu'au » et « de retour le », l'héritage d'une fermeture de société, et
surtout le critère de recherche qui, sans méthode `search`, rendrait TOUTE la
base aussi bien pour « absent » que pour « présent ».
"""

from datetime import date, timedelta

from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "bf_absence")
class TestContactAbsence(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Absence = cls.env["bf.partner.absence"]
        cls.today = date(2026, 9, 12)
        cls.societe = cls.env["res.partner"].create({
            "name": "Societe Essai Inc.",
            "is_company": True,
            "email": "info@societe-essai.test",
        })
        cls.line = cls.env["res.partner"].create({
            "name": "Line Essai",
            "parent_id": cls.societe.id,
            "email": "line@societe-essai.test",
        })
        cls.justin = cls.env["res.partner"].create({
            "name": "Justin Releve",
            "parent_id": cls.societe.id,
            "email": "justin@societe-essai.test",
        })
        cls.tiers = cls.env["res.partner"].create({
            "name": "Personne Presente",
            "email": "present@ailleurs.test",
        })

    def _absence(self, partner, debut, fin, **kw):
        vals = {
            "partner_id": partner.id,
            "date_from": debut,
            "date_to": fin,
        }
        vals.update(kw)
        return self.Absence.create(vals)

    # ------------------------------------------------------------------
    # La période elle-même
    # ------------------------------------------------------------------
    def test_la_date_de_retour_est_le_lendemain_du_dernier_jour_absent(self):
        absence = self._absence(self.line, date(2026, 9, 1), date(2026, 9, 15))
        self.assertEqual(absence.date_return, date(2026, 9, 16))

    def test_saisir_la_date_de_retour_corrige_la_date_de_fin(self):
        """« Je suis de retour le 4 mai » veut dire absent jusqu'au 3.

        C'est l'erreur d'un jour qui ferait parler le bandeau le matin même du
        retour, c'est-à-dire exactement quand il ne faut plus.
        """
        absence = self._absence(self.line, date(2026, 5, 1), date(2026, 5, 10))
        absence.date_return = date(2026, 5, 4)
        absence.flush_recordset()
        self.assertEqual(absence.date_to, date(2026, 5, 3))

    def test_une_absence_sans_fin_est_refusee(self):
        with self.assertRaises(Exception):
            self.Absence.create({
                "partner_id": self.line.id,
                "date_from": date(2026, 9, 1),
            })

    def test_une_fin_avant_le_debut_est_refusee(self):
        with self.assertRaises(ValidationError):
            self._absence(self.line, date(2026, 9, 10), date(2026, 9, 1))

    def test_deux_absences_qui_se_chevauchent_sont_refusees(self):
        self._absence(self.line, date(2026, 9, 1), date(2026, 9, 15))
        with self.assertRaises(ValidationError):
            self._absence(self.line, date(2026, 9, 10), date(2026, 9, 20))

    def test_la_releve_ne_peut_pas_etre_la_personne_absente(self):
        with self.assertRaises(ValidationError):
            self._absence(self.line, date(2026, 9, 1), date(2026, 9, 15),
                          backup_partner_id=self.line.id)

    # ------------------------------------------------------------------
    # Qui est averti
    # ------------------------------------------------------------------
    def test_un_contact_absent_aujourd_hui_est_signale(self):
        aujourd_hui = date.today()
        self._absence(self.line, aujourd_hui - timedelta(days=2),
                      aujourd_hui + timedelta(days=3),
                      backup_partner_id=self.justin.id)
        self.line.invalidate_recordset()
        self.assertTrue(self.line.bf_is_away)
        self.assertEqual(self.line.bf_away_until, aujourd_hui + timedelta(days=3))
        self.assertIn("Justin Releve", self.line.bf_absence_phrase)

    def test_une_absence_passee_ne_signale_rien(self):
        aujourd_hui = date.today()
        self._absence(self.line, aujourd_hui - timedelta(days=30),
                      aujourd_hui - timedelta(days=20))
        self.line.invalidate_recordset()
        self.assertFalse(self.line.bf_is_away)

    def test_une_fermeture_de_societe_avertit_pour_ses_contacts(self):
        aujourd_hui = date.today()
        self._absence(self.societe, aujourd_hui, aujourd_hui + timedelta(days=5),
                      nature="closure")
        self.line.invalidate_recordset()
        self.assertTrue(self.line.bf_is_away)
        self.assertIn("fermeture", self.line.bf_absence_phrase)
        self.assertIn("Societe Essai", self.line.bf_absence_phrase)

    def test_une_fermeture_qui_ne_vaut_que_pour_la_societe(self):
        aujourd_hui = date.today()
        self._absence(self.societe, aujourd_hui, aujourd_hui + timedelta(days=5),
                      nature="closure", applies_to_children=False)
        self.line.invalidate_recordset()
        self.assertFalse(self.line.bf_is_away)
        self.societe.invalidate_recordset()
        self.assertTrue(self.societe.bf_is_away)

    def test_l_absence_propre_gagne_sur_celle_de_la_societe(self):
        aujourd_hui = date.today()
        self._absence(self.societe, aujourd_hui, aujourd_hui + timedelta(days=30),
                      nature="closure")
        self._absence(self.line, aujourd_hui, aujourd_hui + timedelta(days=2),
                      nature="vacation")
        self.line.invalidate_recordset()
        self.assertEqual(self.line.bf_away_until, aujourd_hui + timedelta(days=2))
        self.assertIn("absence", self.line.bf_absence_phrase)

    # ------------------------------------------------------------------
    # 🔴 Le critère de recherche
    # ------------------------------------------------------------------
    def test_le_filtre_absent_ne_rend_pas_toute_la_base(self):
        """Sans méthode `search`, un champ calculé non stocké voit son critère
        écarté en silence : `= True` et `= False` rendraient la base entière.
        """
        aujourd_hui = date.today()
        self._absence(self.line, aujourd_hui, aujourd_hui + timedelta(days=3))
        Partner = self.env["res.partner"]
        absents = Partner.search([("bf_is_away", "=", True)])
        presents = Partner.search([("bf_is_away", "=", False)])
        self.assertIn(self.line, absents)
        self.assertNotIn(self.tiers, absents)
        self.assertIn(self.tiers, presents)
        self.assertNotIn(self.line, presents)
        self.assertNotEqual(len(absents), Partner.search_count([]))

    def test_le_filtre_par_date_de_fin_compare_vraiment(self):
        aujourd_hui = date.today()
        self._absence(self.line, aujourd_hui, aujourd_hui + timedelta(days=3))
        Partner = self.env["res.partner"]
        tot = Partner.search([
            ("bf_away_until", "<=", aujourd_hui + timedelta(days=1))])
        tard = Partner.search([
            ("bf_away_until", ">=", aujourd_hui + timedelta(days=1))])
        self.assertNotIn(self.line, tot)
        self.assertIn(self.line, tard)

    # ------------------------------------------------------------------
    # Le rappel de reprise
    # ------------------------------------------------------------------
    def test_le_rappel_tombe_au_lendemain_du_retour(self):
        absence = self._absence(self.line, date(2026, 9, 1), date(2026, 9, 15))
        self.assertTrue(absence.reminder_activity_id)
        self.assertEqual(absence.reminder_activity_id.date_deadline,
                         date(2026, 9, 17))
        self.assertEqual(absence.reminder_activity_id.res_id, self.line.id)

    def test_le_rappel_suit_la_date_quand_elle_bouge(self):
        absence = self._absence(self.line, date(2026, 9, 1), date(2026, 9, 15))
        absence.date_to = date(2026, 9, 20)
        self.assertEqual(absence.reminder_activity_id.date_deadline,
                         date(2026, 9, 22))

    def test_decocher_le_rappel_le_retire(self):
        absence = self._absence(self.line, date(2026, 9, 1), date(2026, 9, 15))
        activite = absence.reminder_activity_id
        absence.reminder = False
        self.assertFalse(activite.exists())

    def test_supprimer_l_absence_retire_le_rappel(self):
        absence = self._absence(self.line, date(2026, 9, 1), date(2026, 9, 15))
        activite = absence.reminder_activity_id
        absence.unlink()
        self.assertFalse(activite.exists())

    # ------------------------------------------------------------------
    # Les surfaces d'envoi
    # ------------------------------------------------------------------
    def test_le_composeur_avertit_pour_un_destinataire_absent(self):
        aujourd_hui = date.today()
        self._absence(self.line, aujourd_hui, aujourd_hui + timedelta(days=3),
                      backup_partner_id=self.justin.id)
        composeur = self.env["mail.compose.message"].with_context(
            active_model="res.partner", active_ids=[self.line.id],
            active_id=self.line.id,
        ).create({
            "composition_mode": "comment",
            "model": "res.partner",
            "res_ids": str([self.line.id]),
            "subject": "Essai",
            "body": "<p>Essai</p>",
            "partner_ids": [(6, 0, [self.line.id])],
        })
        self.assertTrue(composeur.bf_absence_hint_html)
        self.assertIn("Justin Releve", composeur.bf_absence_hint_html)
        self.assertEqual(composeur.bf_absence_return_date,
                         aujourd_hui + timedelta(days=4))

    def test_le_composeur_reste_muet_sans_absence(self):
        composeur = self.env["mail.compose.message"].with_context(
            active_model="res.partner", active_ids=[self.tiers.id],
            active_id=self.tiers.id,
        ).create({
            "composition_mode": "comment",
            "model": "res.partner",
            "res_ids": str([self.tiers.id]),
            "subject": "Essai",
            "body": "<p>Essai</p>",
            "partner_ids": [(6, 0, [self.tiers.id])],
        })
        self.assertFalse(composeur.bf_absence_hint_html)

    def test_programmer_au_retour_range_le_message_dans_les_envois_differes(self):
        aujourd_hui = date.today()
        self._absence(self.line, aujourd_hui, aujourd_hui + timedelta(days=3))
        composeur = self.env["mail.compose.message"].with_context(
            active_model="res.partner", active_ids=[self.line.id],
            active_id=self.line.id,
        ).create({
            "composition_mode": "comment",
            "model": "res.partner",
            "res_ids": str([self.line.id]),
            "subject": "Essai",
            "body": "<p>Essai</p>",
            "partner_ids": [(6, 0, [self.line.id])],
        })
        avant = self.env["mail.scheduled.message"].search([])
        composeur.action_bf_send_on_return()
        apres = self.env["mail.scheduled.message"].search([]) - avant
        self.assertEqual(len(apres), 1)
        self.assertEqual(apres.res_id, self.line.id)
        self.assertEqual(apres.scheduled_date.date(),
                         aujourd_hui + timedelta(days=4))

    def test_l_heure_du_report_est_locale_et_non_utc(self):
        """Neuf heures veut dire neuf heures au Québec.

        Sans passage par le fuseau, un envoi reporté partirait à cinq heures du
        matin, heure locale.
        """
        quand = self.Absence.with_context(tz="America/Toronto")._return_datetime(
            "2026-09-16")
        self.assertEqual(str(quand), "2026-09-16 13:00:00")

    def test_l_indice_du_chatter_lit_la_fiche_du_contact(self):
        aujourd_hui = date.today()
        self._absence(self.line, aujourd_hui, aujourd_hui + timedelta(days=3))
        charge = self.Absence.hint_for_thread("res.partner", self.line.id)
        self.assertEqual(len(charge["lines"]), 1)
        self.assertTrue(charge["return_date"])
        vide = self.Absence.hint_for_thread("res.partner", self.tiers.id)
        self.assertEqual(vide["lines"], [])

    def test_l_indice_du_chatter_ignore_un_modele_inconnu(self):
        charge = self.Absence.hint_for_thread("modele.qui.n.existe.pas", 1)
        self.assertEqual(charge["lines"], [])

    def test_l_indice_du_chatter_ecarte_les_abonnes_internes(self):
        """On n'avertit pas qu'un COLLÈGUE est en vacances quand on écrit au
        client depuis le chatter d'une fiche."""
        aujourd_hui = date.today()
        interne = self.env["res.users"].create({
            "name": "Collegue Interne",
            "login": "collegue.absence.test",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])],
        })
        self._absence(interne.partner_id, aujourd_hui,
                      aujourd_hui + timedelta(days=3))
        fiche = self.env["res.partner"].create({"name": "Dossier client"})
        fiche.message_subscribe(partner_ids=[interne.partner_id.id])
        charge = self.Absence.hint_for_thread("res.partner", fiche.id)
        self.assertEqual(charge["lines"], [])


@tagged("post_install", "-at_install", "bf_absence")
class TestAuditAbsence(TransactionCase):
    """Les quatre défauts fermés à l'audit adverse du 2026-09-13.

    Chacun est ici pour ne pas revenir : ils ont tous été trouvés en essayant
    de casser le module, aucun en le relisant.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Absence = cls.env["bf.partner.absence"]
        cls.contact = cls.env["res.partner"].create({
            "name": "Audit Contact", "email": "audit@essai-audit.test"})
        cls.today = date.today()

    def test_rentre_plus_tot_le_jour_meme_eteint_l_avertissement(self):
        """🔴 La première version laissait la personne absente pour la journée
        en cours : le bouton dit « rentré », il doit rendre présent."""
        absence = self.Absence.create({
            "partner_id": self.contact.id,
            "date_from": self.today,
            "date_to": self.today + timedelta(days=5)})
        absence.action_end_now()
        self.contact.invalidate_recordset()
        self.assertFalse(self.contact.bf_is_away)

    def test_rentre_plus_tot_apres_quelques_jours_garde_l_historique(self):
        absence = self.Absence.create({
            "partner_id": self.contact.id,
            "date_from": self.today - timedelta(days=4),
            "date_to": self.today + timedelta(days=5)})
        absence.action_end_now()
        self.contact.invalidate_recordset()
        self.assertFalse(self.contact.bf_is_away)
        self.assertTrue(absence.active)
        self.assertEqual(absence.date_to, self.today - timedelta(days=1))

    def test_la_cloison_de_societes_vaut_aussi_pour_le_bandeau(self):
        """🔴 L'audit a montré une absence invisible à la LISTE d'un usager
        d'une autre société, et pourtant affichée dans son BANDEAU."""
        autre_societe = self.env["res.company"].create({"name": "Audit Societe B"})
        self.Absence.create({
            "partner_id": self.contact.id,
            "date_from": self.today,
            "date_to": self.today + timedelta(days=3),
            "company_id": autre_societe.id})
        vu = self.contact.with_company(self.env.company)
        vu.invalidate_recordset()
        self.assertFalse(vu.bf_is_away)
        chez_eux = self.contact.with_context(
            allowed_company_ids=[autre_societe.id]).with_company(autre_societe)
        chez_eux.invalidate_recordset()
        self.assertTrue(chez_eux.bf_is_away)

    def test_un_utilisateur_externe_ne_voit_aucune_absence(self):
        """Savoir qui est en vacances chez nos clients est interne."""
        portail = self.env["res.users"].create({
            "name": "Audit Portail", "login": "audit.portail.essai",
            "groups_id": [(6, 0, [self.env.ref("base.group_portal").id])]})
        self.Absence.create({
            "partner_id": portail.partner_id.id,
            "date_from": self.today,
            "date_to": self.today + timedelta(days=3)})
        fiche = portail.partner_id.with_user(portail)
        fiche.invalidate_recordset()
        self.assertFalse(fiche.bf_is_away)
        self.assertFalse(fiche.bf_absence_phrase)
        charge = self.Absence.with_user(portail)._hint_for_partners(
            [portail.partner_id.id])
        self.assertEqual(charge["lines"], [])

    def test_l_indice_du_chatter_ne_leve_jamais_meme_sans_droits(self):
        """🔴 L'audit a fait lever une AccessError sur `message_follower_ids`
        pour un compte portail : un avertissement qui lève casse la surface
        qu'il devait servir."""
        portail = self.env["res.users"].create({
            "name": "Audit Portail 2", "login": "audit.portail.essai2",
            "groups_id": [(6, 0, [self.env.ref("base.group_portal").id])]})
        charge = self.Absence.with_user(portail).hint_for_thread(
            "res.partner", self.contact.id)
        self.assertEqual(charge["lines"], [])

    def test_le_bandeau_compte_au_dela_de_trois_absents(self):
        """🔴 Vingt-cinq destinataires absents rendaient 2 440 caractères :
        ce n'est plus un avertissement, c'est un mur."""
        gens = self.env["res.partner"].create([
            {"name": "Audit Masse %d" % i,
             "email": "masse%d@essai-audit.test" % i} for i in range(7)])
        self.Absence.create([
            {"partner_id": g.id, "date_from": self.today,
             "date_to": self.today + timedelta(days=2)} for g in gens])
        charge = self.Absence._hint_for_partners(gens.ids)
        self.assertEqual(len(charge["lines"]), 4)
        self.assertIn("4", charge["lines"][-1])

    def test_trois_absents_tiennent_sans_compteur(self):
        gens = self.env["res.partner"].create([
            {"name": "Audit Trio %d" % i,
             "email": "trio%d@essai-audit.test" % i} for i in range(3)])
        self.Absence.create([
            {"partner_id": g.id, "date_from": self.today,
             "date_to": self.today + timedelta(days=2)} for g in gens])
        charge = self.Absence._hint_for_partners(gens.ids)
        self.assertEqual(len(charge["lines"]), 3)
        self.assertNotIn("autre(s)", " ".join(charge["lines"]))

    def test_la_phrase_nomme_la_personne_et_pas_la_ligne_de_formulaire(self):
        """🔴 `display_name` d'un contact rattaché commence par la société :
        le bandeau lisait « Société Exemple Inc., Prénom Nom est absent »,
        une ligne de formulaire au lieu d'une phrase."""
        societe = self.env["res.partner"].create({
            "name": "Audit Phrase Inc.", "is_company": True})
        personne = self.env["res.partner"].create({
            "name": "Camille Phrase", "parent_id": societe.id})
        absence = self.Absence.create({
            "partner_id": personne.id,
            "date_from": self.today,
            "date_to": self.today + timedelta(days=3)})
        phrase = absence._phrase()
        self.assertTrue(phrase.startswith("Camille Phrase"), phrase)
        # 🔴 Aucun accord en genre : Odoo ne porte pas le genre d'un contact.
        for accorde in (" est absent", " est absente", " est fermé", " est fermée"):
            self.assertNotIn(accorde, phrase, phrase)
        self.assertNotIn("Audit Phrase Inc.,", phrase)

    def test_la_phrase_donne_une_date_lisible_et_non_ambigue(self):
        """🔴 « jusqu'au 09/17/2026 » : 09/17 se décode tout seul, 09/11 non."""
        absence = self.Absence.create({
            "partner_id": self.contact.id,
            "date_from": date(2026, 9, 10),
            "date_to": date(2026, 9, 11)})
        phrase = absence._phrase()
        self.assertNotIn("/", phrase, phrase)
        self.assertIn("11", phrase)


@tagged("post_install", "-at_install", "bf_absence")
class TestQuickWinsAbsence(TransactionCase):
    """Le semis des fermetures connues, et le rappel qui porte une prise."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Absence = cls.env["bf.partner.absence"]
        cls.Wizard = cls.env["bf.absence.closure.wizard"]
        cls.secteur = cls.env["res.partner.industry"].create(
            {"name": "Construction Essai"})
        cls.chantier = cls.env["res.partner"].create({
            "name": "Chantiers Essai Inc.", "is_company": True,
            "industry_id": cls.secteur.id, "email": "info@chantiers.test"})
        cls.ouvrier = cls.env["res.partner"].create({
            "name": "Ouvrier Essai", "parent_id": cls.chantier.id})
        cls.muette = cls.env["res.partner"].create({
            "name": "Societe Sans Contact", "is_company": True,
            "industry_id": cls.secteur.id})
        cls.today = date.today()

    # ---- Le semis des fermetures ------------------------------------
    def test_le_semis_pose_la_fermeture_sur_la_societe(self):
        wizard = self.Wizard.create({"closure": "estival_2027",
                                     "partner_ids": [(6, 0, self.chantier.ids)]})
        self.assertEqual(wizard.date_from, date(2027, 7, 25))
        self.assertEqual(wizard.date_to, date(2027, 8, 7))
        wizard.action_seed()
        absence = self.Absence.search([("partner_id", "=", self.chantier.id)])
        self.assertEqual(len(absence), 1)
        self.assertEqual(absence.nature, "closure")
        self.assertEqual(absence.source, "import")
        self.assertTrue(absence.applies_to_children)
        self.assertFalse(absence.reminder)

    def test_la_fermeture_avertit_pour_les_contacts_de_la_societe(self):
        """Mesuré : trois personnes de la même entreprise ont répondu la même
        fermeture. Une seule ligne doit suffire à les couvrir toutes."""
        wizard = self.Wizard.create({
            "closure": "hivernal_2026",
            "partner_ids": [(6, 0, self.chantier.ids)]})
        wizard.action_seed()
        absences = self.Absence._for_partners(self.ouvrier.ids,
                                              at_date=date(2026, 12, 24))
        self.assertIn(self.ouvrier.id, absences)
        self.assertIn("fermeture", absences[self.ouvrier.id]._phrase(
            for_partner=self.ouvrier))

    def test_le_semis_par_secteur_prend_tout_le_secteur(self):
        wizard = self.Wizard.create({"closure": "estival_2027",
                                     "industry_id": self.secteur.id,
                                     "only_with_contacts": False})
        wizard.action_seed()
        posees = self.Absence.search([
            ("partner_id", "in", (self.chantier | self.muette).ids)])
        self.assertEqual(len(posees), 2)

    def test_le_semis_ecarte_les_societes_a_qui_on_n_ecrit_jamais(self):
        wizard = self.Wizard.create({"closure": "estival_2027",
                                     "industry_id": self.secteur.id,
                                     "only_with_contacts": True})
        wizard.action_seed()
        self.assertTrue(self.Absence.search(
            [("partner_id", "=", self.chantier.id)]))
        self.assertFalse(self.Absence.search(
            [("partner_id", "=", self.muette.id)]))

    def test_le_semis_ne_double_jamais_une_periode_deja_connue(self):
        """Une absence apprise d'un répondeur, ou saisie à la main, gagne."""
        deja = self.Absence.create({
            "partner_id": self.chantier.id,
            "date_from": date(2027, 7, 26), "date_to": date(2027, 7, 30),
            "nature": "closure"})
        wizard = self.Wizard.create({"closure": "estival_2027",
                                     "partner_ids": [(6, 0, self.chantier.ids)]})
        action = wizard.action_seed()
        self.assertEqual(action.get("tag"), "display_notification")
        restantes = self.Absence.search([("partner_id", "=", self.chantier.id)])
        self.assertEqual(restantes, deja)

    def test_le_semis_refuse_de_tourner_a_vide(self):
        wizard = self.Wizard.create({"closure": "estival_2027"})
        with self.assertRaises(UserError):
            wizard.action_seed()

    def test_la_fermeture_proposee_n_est_jamais_deja_passee(self):
        """Proposer le congé de l'été dernier serait un piège à clic."""
        cle = self.Wizard._default_closure()
        wizard = self.Wizard.create({"closure": cle})
        self.assertGreaterEqual(wizard.date_to, date.today())

    # ---- Le rappel qui porte une prise -------------------------------
    def test_le_rappel_de_reprise_nomme_le_dernier_echange(self):
        """Un rappel « prendre des nouvelles » sans sujet se fait reporter."""
        contact = self.env["res.partner"].create({
            "name": "Client Suivi", "email": "suivi@essai-qw.test"})
        self.env["mail.message"].create({
            "model": "res.partner", "res_id": contact.id,
            "message_type": "email", "subject": "Devis 2026-0042",
            "body": "<p>Voici le devis.</p>",
            "partner_ids": [(6, 0, contact.ids)]})
        absence = self.Absence.create({
            "partner_id": contact.id,
            "date_from": self.today, "date_to": self.today + timedelta(days=3)})
        note = absence.reminder_activity_id.note or ""
        self.assertIn("Devis 2026-0042", note)
        self.assertIn("Client Suivi", note)

    def test_le_rappel_tient_sans_aucun_echange(self):
        contact = self.env["res.partner"].create({"name": "Client Muet"})
        absence = self.Absence.create({
            "partner_id": contact.id,
            "date_from": self.today, "date_to": self.today + timedelta(days=3)})
        self.assertTrue(absence.reminder_activity_id)
        self.assertIn("Client Muet", absence.reminder_activity_id.note or "")
