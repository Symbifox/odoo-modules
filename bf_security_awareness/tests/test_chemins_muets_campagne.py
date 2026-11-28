"""Trois chemins d'erreur muets autour de la campagne.

Même famille : « un chemin d'erreur muet ».

1. **Le lancement planifié qui échoue** ne laissait qu'une ligne au journal du
   serveur. Il écrit maintenant une note au fil, pose une activité chez qui a
   créé la campagne, et vide la date planifiée : la tâche cesse de réessayer.
2. **Une campagne en cours qui lève** arrêtait la distribution de TOUTES les
   autres. Chaque campagne est maintenant isolée, et la fautive le dit au fil,
   une fois tant que la même erreur revient.
3. **Le cours non publié** : la page publique envoyait l'employé pris par la
   simulation vers une page qu'il ne peut pas ouvrir. Le lancement refuse, et
   la page n'offre le bouton que vers un cours publié.

⚠️ Trois pièges de harnais :

- ``assertRaises`` d'Odoo entre un point de sauvegarde et le DÉFAIT : un état
  lu après lui est celui d'avant le bloc. Les essais d'état emploient un
  ``try/except`` nu.
- Sous les essais, ``_isoler`` prend la branche du point de sauvegarde ; la
  branche de production (valider, annuler) ne s'éprouve QUE hors essai — elle
  se mesure dans un ``odoo shell``, pas ici.
- Une erreur de la BASE se provoque pour de vrai (``SELECT 1/0``) : une
  exception Python simulée n'abandonne pas la transaction et ne prouve rien du
  point de sauvegarde.
"""
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged
from odoo.tools import html2plaintext

from odoo.addons.bf_security_awareness.models.bf_phishing_campaign import (
    BfPhishingCampaign,
)


class CampagneCommun(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Cours = cls.env["slide.channel"]
        commun = {"channel_type": "training", "enroll": "invite",
                  "visibility": "connected"}
        cls.publie = Cours.create(dict(commun, name="Hameçonnage essai (publié)"))
        cls.publie.is_published = True
        cls.prepa = Cours.create(dict(
            commun, name="Hameçonnage essai (en préparation)"))
        cls.prepa.is_published = False
        cls.operateur = new_test_user(
            cls.env, login="operateur_essai", name="Opératrice essai",
            email="operatrice.essai@exemple.test",
            groups="base.group_user,bf_security_awareness.group_bf_secaware_manager")
        cls.cible = cls.env["res.partner"].create({
            "name": "Alice essai", "email": "alice.essai@exemple.test"})

    # -- outils ---------------------------------------------------------------
    def _leurre(self, cours):
        return self.env["bf.phishing.template"].create({
            "name": "Leurre essai",
            "subject": "Action requise",
            "landing_mode": "credential",
            "training_channel_id": cours.id if cours else False,
        })

    def _campagne(self, cours, nom="Campagne essai", par=None):
        Campagne = self.env["bf.phishing.campaign"]
        if par:
            Campagne = Campagne.with_user(par)
        camp = Campagne.create({
            "name": nom,
            "template_id": self._leurre(cours).id,
            "recipient_partner_ids": [(6, 0, self.cible.ids)],
        })
        camp = camp.with_user(self.env.user)
        camp.action_prepare()
        return camp

    def _planifiee(self, cours, **kw):
        camp = self._campagne(cours, **kw)
        camp.scheduled_date = fields.Datetime.now() - timedelta(minutes=5)
        return camp

    def _notes(self, camp):
        return self.env["mail.message"].search([
            ("model", "=", camp._name), ("res_id", "=", camp.id),
            ("subtype_id", "=", self.env.ref("mail.mt_note").id),
        ], order="id desc").filtered(lambda m: html2plaintext(m.body or "").strip())


@tagged("post_install", "-at_install")
class TestLancementRefuse(CampagneCommun):
    """Constat 3, côté modèle : le lancement refuse un cours non publié."""

    def test_lancement_refuse_cours_non_publie(self):
        camp = self._campagne(self.prepa)
        with self.assertRaises(UserError):
            camp.action_launch()

    def test_refus_ne_laisse_rien(self):
        camp = self._campagne(self.prepa)
        try:
            camp.action_launch()
        except UserError:
            pass
        self.env.invalidate_all()
        self.assertEqual(camp.state, "scheduled")
        self.assertFalse(camp.result_ids.filtered("scheduled_send"))
        self.assertEqual(set(camp.result_ids.mapped("state")), {"pending"})

    def test_refus_ne_nomme_pas_le_cours(self):
        camp = self._campagne(self.prepa)
        try:
            camp.action_launch()
        except UserError as exc:
            message = str(exc)
        else:
            self.fail("le lancement aurait dû être refusé")
        self.assertNotIn(self.prepa.name, message)

    def test_refus_avant_le_premier_effet_du_lot(self):
        bonne = self._campagne(self.publie, nom="Bonne essai")
        mauvaise = self._campagne(self.prepa, nom="Mauvaise essai")
        try:
            (bonne | mauvaise).action_launch()
        except UserError:
            pass
        self.env.invalidate_all()
        self.assertEqual(bonne.state, "scheduled",
                         "la campagne qui précède a été lancée avant le refus")

    def test_cours_publie_le_lancement_part(self):
        camp = self._campagne(self.publie)
        camp.action_launch()
        self.assertEqual(camp.state, "running")

    def test_sans_cours_le_lancement_part(self):
        camp = self._campagne(False)
        camp.action_launch()
        self.assertEqual(camp.state, "running")

    def test_la_preparation_reste_permise(self):
        camp = self._campagne(self.prepa)
        self.assertEqual(camp.state, "scheduled")

    def test_refus_lu_par_l_operateur_sans_lire_le_cours(self):
        """L'Opératrice ne lit pas un cours non publié : le refus se calcule
        quand même (lecture en sudo), et ne lève pas d'AccessError."""
        camp = self._campagne(self.prepa)
        self.env.invalidate_all()
        try:
            camp.with_user(self.operateur).action_launch()
        except UserError as exc:
            self.assertNotIn("access", type(exc).__name__.lower())
        else:
            self.fail("le lancement aurait dû être refusé")


@tagged("post_install", "-at_install")
class TestLancementPlanifieEchoue(CampagneCommun):
    """Constat 1 : le lancement planifié qui échoue se dit, et cesse."""

    def _cron(self):
        self.env["bf.phishing.campaign"]._cron_scheduler()
        self.env.invalidate_all()

    def test_echec_ecrit_au_fil_et_date_videe(self):
        camp = self._planifiee(self.prepa)
        avant = len(self._notes(camp))
        self._cron()
        self.assertEqual(camp.state, "scheduled")
        self.assertFalse(camp.scheduled_date,
                         "la tâche planifiée réessaierait toutes les 30 minutes")
        self.assertEqual(len(self._notes(camp)), avant + 1)

    def test_la_note_porte_la_cause(self):
        camp = self._planifiee(self.prepa)
        refus = camp._refus_lancement()
        self._cron()
        texte = html2plaintext(self._notes(camp)[:1].body)
        self.assertIn(refus.rstrip("."), texte)
        # Le refus finit par un point, et la phrase qui le cite en ajoute un.
        self.assertNotIn("..", texte)

    def test_la_note_est_interne(self):
        """La note d'échec est INTERNE : elle ne part vers aucun abonné."""
        camp = self._planifiee(self.prepa)
        refus = camp._refus_lancement()
        self._cron()
        notes = self.env["mail.message"].search([
            ("model", "=", camp._name), ("res_id", "=", camp.id)]).filtered(
                lambda m: refus in html2plaintext(m.body or ""))
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes.subtype_id, self.env.ref("mail.mt_note"))

    def test_activite_chez_qui_a_cree(self):
        camp = self._planifiee(self.prepa, par=self.operateur)
        self.assertEqual(camp.create_uid, self.operateur)
        self._cron()
        activites = camp.activity_ids.filtered(
            lambda a: a.user_id == self.operateur)
        self.assertEqual(len(activites), 1)
        self.assertEqual(activites.activity_type_id,
                         self.env.ref("mail.mail_activity_data_todo"))

    def test_pas_d_activite_pour_un_createur_hors_d_atteinte(self):
        """Créée par le système (inactif) : la note seule, sans lever."""
        camp = self._planifiee(self.prepa)
        self.assertFalse(camp.create_uid.active,
                         "l'essai suppose un créateur inactif (__system__)")
        self._cron()
        self.assertFalse(camp.scheduled_date)
        self.assertFalse(camp.activity_ids)
        self.assertTrue(self._notes(camp))

    def test_la_passe_suivante_ne_reessaie_pas(self):
        """Mesuré sur l'appel lui-même, pas sur les notes : l'ancien code
        n'écrivait rien au fil, et « aucune note de plus » y passait aussi."""
        # Une panne qui en est une sur l'ancien code comme sur le neuf : un
        # cours non publié, lui, laissait partir la campagne avant ce correctif.
        camp = self._planifiee(self.publie)
        appels = []

        def en_panne(self):
            appels.extend(self.ids)
            raise RuntimeError("panne essai")

        with patch.object(BfPhishingCampaign, "action_launch", en_panne):
            self._cron()
            self.assertEqual(appels, [camp.id])
            self._cron()
        self.assertEqual(appels, [camp.id],
                         "la tâche planifiée a réessayé le lancement")

    def test_erreur_python_en_chemin_est_defaite(self):
        """Une erreur APRÈS le passage à « sending » ne laisse pas la campagne
        coincée : le point de sauvegarde défait ce que le lancement avait
        écrit."""
        camp = self._planifiee(self.publie)

        def boum(self):
            raise RuntimeError("boum essai")

        with patch.object(BfPhishingCampaign, "_release_due_results", boum):
            self._cron()
        self.assertEqual(camp.state, "scheduled")
        self.assertFalse(camp.result_ids.filtered("scheduled_send"))
        self.assertIn("boum essai", self._notes(camp)[:1].body)

    def test_erreur_de_la_base_ne_casse_ni_la_note_ni_les_autres(self):
        """Une vraie erreur de la base abandonne la transaction : sans point de
        sauvegarde, la note lèverait à son tour, et la campagne suivante ne
        partirait pas."""
        fautive = self._planifiee(self.publie, nom="Fautive essai")
        saine = self._planifiee(self.publie, nom="Saine essai")
        original = BfPhishingCampaign._release_due_results

        def base_en_erreur(self):
            if self.id == fautive.id:
                self.env.cr.execute("SELECT 1/0")
            return original(self)

        with patch.object(BfPhishingCampaign, "_release_due_results",
                          base_en_erreur):
            self._cron()
        self.assertIn("division by zero", self._notes(fautive)[:1].body)
        self.assertEqual(fautive.state, "scheduled")
        self.assertEqual(saine.state, "running")

    def test_la_campagne_suivante_part_quand_meme(self):
        fautive = self._planifiee(self.prepa, nom="Fautive essai")
        saine = self._planifiee(self.publie, nom="Saine essai")
        self._cron()
        self.assertEqual(fautive.state, "scheduled")
        self.assertEqual(saine.state, "running")


@tagged("post_install", "-at_install")
class TestCampagnesEnCoursIsolees(CampagneCommun):
    """Constat 2 : une campagne en cours qui lève n'emporte pas les autres."""

    def _en_cours(self, nom):
        camp = self._campagne(self.publie, nom=nom)
        camp.send_window_days = 3
        camp.action_launch()
        self.assertEqual(camp.state, "running")
        # Rendre un envoi dû à la prochaine passe.
        camp.result_ids.write({
            "state": "pending",
            "scheduled_send": fields.Datetime.now() - timedelta(minutes=1)})
        return camp

    def _cron(self):
        self.env["bf.phishing.campaign"]._cron_scheduler()
        self.env.invalidate_all()

    def _avec_une_fautive(self, fautive, erreur):
        original = BfPhishingCampaign._release_due_results

        def en_erreur(self):
            if self.id == fautive.id:
                erreur(self)
            return original(self)

        return patch.object(
            BfPhishingCampaign, "_release_due_results", en_erreur)

    def test_les_autres_continuent(self):
        fautive = self._en_cours("Fautive essai")
        saine = self._en_cours("Saine essai")

        def boum(camp):
            raise RuntimeError("boum essai")

        with self._avec_une_fautive(fautive, boum):
            self._cron()
        self.assertEqual(set(saine.result_ids.mapped("state")), {"sent"})
        self.assertEqual(set(fautive.result_ids.mapped("state")), {"pending"})

    def test_la_reconciliation_des_traces_continue(self):
        fautive = self._en_cours("Fautive essai")
        saine = self._en_cours("Saine essai")
        vus = []
        Resultat = type(self.env["bf.phishing.result"])
        original = Resultat._sync_from_traces

        def temoin(self):
            vus.extend(self.ids)
            return original(self)

        def boum(camp):
            raise RuntimeError("boum essai")

        with self._avec_une_fautive(fautive, boum), \
                patch.object(Resultat, "_sync_from_traces", temoin):
            self._cron()
        self.assertTrue(set(saine.result_ids.ids) <= set(vus))

    def test_la_fautive_le_dit_au_fil(self):
        fautive = self._en_cours("Fautive essai")
        avant = len(self._notes(fautive))

        def boum(camp):
            raise RuntimeError("boum essai")

        with self._avec_une_fautive(fautive, boum):
            self._cron()
        notes = self._notes(fautive)
        self.assertEqual(len(notes), avant + 1)
        self.assertIn("boum essai", notes[:1].body)
        self.assertEqual(notes[:1].subtype_id, self.env.ref("mail.mt_note"))

    def test_la_meme_erreur_ne_se_repete_pas(self):
        fautive = self._en_cours("Fautive essai")

        def boum(camp):
            raise RuntimeError("boum essai")

        with self._avec_une_fautive(fautive, boum):
            self._cron()
            apres = len(self._notes(fautive))
            self._cron()
            self._cron()
        self.assertEqual(len(self._notes(fautive)), apres)

    def test_une_autre_erreur_se_dit(self):
        fautive = self._en_cours("Fautive essai")

        def boum(camp):
            raise RuntimeError("boum essai")

        def autre(camp):
            raise RuntimeError("autre essai")

        with self._avec_une_fautive(fautive, boum):
            self._cron()
        apres = len(self._notes(fautive))
        with self._avec_une_fautive(fautive, autre):
            self._cron()
        self.assertEqual(len(self._notes(fautive)), apres + 1)
        self.assertIn("autre essai", self._notes(fautive)[:1].body)

    def test_erreur_de_la_base(self):
        fautive = self._en_cours("Fautive essai")
        saine = self._en_cours("Saine essai")

        def division(camp):
            camp.env.cr.execute("SELECT 1/0")

        with self._avec_une_fautive(fautive, division):
            self._cron()
        self.assertIn("division by zero", self._notes(fautive)[:1].body)
        self.assertEqual(set(saine.result_ids.mapped("state")), {"sent"})


@tagged("post_install", "-at_install")
class TestPageFormation(HttpCase):
    """Constat 3, côté page : le bouton ne mène qu'à un cours publié."""

    def _page(self, publie):
        cours = self.env["slide.channel"].create({
            "name": "Cours page essai", "channel_type": "training",
            "enroll": "invite", "visibility": "connected"})
        cours.is_published = publie
        leurre = self.env["bf.phishing.template"].create({
            "name": "Leurre page essai", "subject": "Bonjour",
            "landing_mode": "credential",
            "training_channel_id": cours.id})
        cible = self.env["res.partner"].create({
            "name": "Bob essai", "email": "bob.essai@exemple.test"})
        camp = self.env["bf.phishing.campaign"].create({
            "name": "Page essai", "template_id": leurre.id,
            "recipient_partner_ids": [(6, 0, cible.ids)]})
        camp.action_prepare()
        resp = self.url_open("/phish/%s/lesson" % camp.result_ids[0].token)
        self.assertEqual(resp.status_code, 200)
        return resp.text, "/slides/%s" % cours.id

    def test_cours_non_publie_pas_de_bouton(self):
        page, lien = self._page(False)
        self.assertNotIn(lien, page)

    def test_cours_publie_le_bouton(self):
        page, lien = self._page(True)
        self.assertIn(lien, page)


@tagged("post_install", "-at_install")
class TestCasLimitesTache(CampagneCommun):
    """Les cas limites de la tâche planifiée, chacun tenu."""

    def _cron(self):
        self.env["bf.phishing.campaign"]._cron_scheduler()
        self.env.invalidate_all()

    def test_c1_un_signalement_qui_leve_n_emporte_pas_la_passe(self):
        fautive = self._planifiee(self.prepa, nom="Fautive essai")
        saine = self._planifiee(self.publie, nom="Saine essai")

        def leve(self, exc):
            raise RuntimeError("signalement en panne essai")

        with patch.object(BfPhishingCampaign, "_signaler_lancement_echoue", leve):
            self._cron()   # ne doit pas lever
        self.assertEqual(saine.state, "running")
        self.assertEqual(fautive.state, "scheduled")

    def test_c2_une_campagne_qui_n_est_plus_due_est_passee_sans_alerte(self):
        camp = self._planifiee(self.publie)
        camp.action_launch()          # lancée à la main entre-temps
        avant = len(self._notes(camp))
        camp._lancer_si_due(fields.Datetime.now())   # ne doit pas lever
        self.assertEqual(len(self._notes(camp)), avant)
        self.assertFalse(camp.activity_ids)

    def test_c2_une_date_repoussee_n_est_pas_lancee(self):
        camp = self._planifiee(self.publie)
        camp.scheduled_date = fields.Datetime.now() + timedelta(days=1)
        camp._lancer_si_due(fields.Datetime.now())
        self.assertEqual(camp.state, "scheduled")

    def test_c3_une_erreur_de_la_base_a_espaces_doubles_ne_se_repete_pas(self):
        camp = self._campagne(self.publie)
        erreur = RuntimeError(
            'null value in column "x" violates not-null constraint\n'
            "DETAIL:  Failing row contains (1, null).")
        camp._noter_echec_moteur(erreur)
        apres = len(self._notes(camp))
        camp._noter_echec_moteur(erreur)
        camp._noter_echec_moteur(erreur)
        self.assertEqual(len(self._notes(camp)), apres)

    def test_c5_une_erreur_de_concurrence_est_reessayee_sans_alerte(self):
        import psycopg2.errors
        camp = self._planifiee(self.publie)
        date = camp.scheduled_date
        avant = len(self._notes(camp))

        def conflit(self):
            raise psycopg2.errors.SerializationFailure("conflit essai")

        with patch.object(BfPhishingCampaign, "action_launch", conflit):
            self._cron()
        self.assertEqual(camp.scheduled_date, date)
        self.assertEqual(len(self._notes(camp)), avant)
        self.assertFalse(camp.activity_ids)

    def test_c6_une_seule_activite_ouverte_si_l_echec_revient(self):
        camp = self._planifiee(self.prepa, par=self.operateur)
        self._cron()
        camp.scheduled_date = fields.Datetime.now() - timedelta(minutes=1)
        self._cron()
        self.assertEqual(len(camp.activity_ids.filtered(
            lambda a: a.user_id == self.operateur)), 1)

    def test_c6_une_exception_sans_texte_est_nommee(self):
        camp = self._planifiee(self.publie)

        def muette(self):
            raise AssertionError()

        with patch.object(BfPhishingCampaign, "action_launch", muette):
            self._cron()
        self.assertIn("AssertionError", self._notes(camp)[:1].body)

    # -- erreurs de concurrence ------------------------------------------------
    def test_t2_une_erreur_de_concurrence_enveloppee_reste_passagere(self):
        """`_release_due_results` pose une note sur une transaction avortée :
        c'est `InFailedSqlTransaction` qui sort, la vraie cause en contexte."""
        import psycopg2.errors
        camp = self._planifiee(self.publie)
        date = camp.scheduled_date

        def enveloppee(self):
            try:
                raise psycopg2.errors.SerializationFailure("conflit essai")
            except psycopg2.errors.SerializationFailure:
                raise RuntimeError("current transaction is aborted essai")  # noqa: B904 - le __context__ implicite est ce qu'on simule

        with patch.object(BfPhishingCampaign, "action_launch", enveloppee):
            self._cron()
        self.assertEqual(camp.scheduled_date, date)
        self.assertFalse(camp.activity_ids)

    def test_t2_une_erreur_de_concurrence_qui_dure_se_dit(self):
        import psycopg2.errors
        camp = self._planifiee(self.publie)
        camp.scheduled_date = fields.Datetime.now() - timedelta(hours=7)

        def conflit(self):
            raise psycopg2.errors.LockNotAvailable("verrou essai")

        with patch.object(BfPhishingCampaign, "action_launch", conflit):
            self._cron()
        self.assertFalse(camp.scheduled_date)
        self.assertIn("verrou essai", self._notes(camp)[:1].body)

    def test_t2_une_campagne_en_cours_dit_aussi_la_concurrence(self):
        """Pas d'exemption pour les campagnes en cours : la note y est
        dédoublonnée, une erreur qui revient ne peut pas rester muette."""
        import psycopg2.errors
        camp = self._campagne(self.publie)
        camp.action_launch()
        avant = len(self._notes(camp))

        def conflit(self):
            raise psycopg2.errors.DeadlockDetected("interblocage essai")

        with patch.object(BfPhishingCampaign, "_release_due_results", conflit):
            self._cron()
        self.assertEqual(len(self._notes(camp)), avant + 1)
