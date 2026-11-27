"""Le questionnaire ouvert depuis la page de progression.

Ce qui compte : le bouton ne sort que s'il y a quelque chose à ouvrir, il sort à
UN seul endroit, le geste mène à LA réponse DU MANDAT — jamais à celle d'un
autre mandat du même payeur —, une réponse finie ne se rouvre pas, et la route
refuse exactement ce que la page refuse.
"""
import re
from datetime import timedelta
from unittest.mock import patch

from psycopg2.errors import SerializationFailure

from odoo import fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import HttpCase, TransactionCase, tagged

from odoo.addons.bf_stepbystep_clients.tests.test_portail import _Mandat

BOUTON = "Fill in the questionnaire"
RECU = "Questionnaire received"
JETON_CSRF = re.compile(r'name="csrf_token"\s+value="([^"]+)"')
JETON_CSRF_SCRIPT = re.compile(r'csrf_token:\s*"([^"]+)"')


class CourseSimulee(SerializationFailure):
    """La course perdue, telle que PostgreSQL la lève.

    ⚠️ Le `pgcode` n'est pas décoratif : `retrying()` d'Odoo le passe à
    `errorcodes.lookup()` dans sa ligne de journal avant de rejouer, et une
    erreur construite à la main sans code faisait lever KeyError LÀ — un 500
    qui se lisait comme un défaut du module, alors que la remontée était juste.
    `pgcode` est en lecture seule sur l'instance ; une sous-classe le fournit.
    """
    pgcode = "40001"  # serialization_failure


class _AvecQuestionnaire(_Mandat):

    @classmethod
    def _questionnaire(cls, env, **valeurs):
        return env["survey.survey"].create(dict({
            "title": "Diagnostic Export",
            "access_mode": "public",
        }, **valeurs))


@tagged("post_install", "-at_install")
class TestBoutonDuQuestionnaire(HttpCase, _AvecQuestionnaire):

    def setUp(self):
        super().setUp()
        self.projet, self.colonnes = self._mandat(self.env)
        self.projet.action_progression_portal_publish()
        self.jeton = self.projet.progression_portal_token
        self.sondage = self._questionnaire(self.env)
        self.Reponses = self.env["survey.user_input"]

    def _page(self, projet=None, jeton=None):
        projet = projet or self.projet
        reponse = self.url_open(
            "/progression/%s/%s" % (projet.id, jeton or projet.progression_portal_token),
            allow_redirects=False)
        self.assertEqual(reponse.status_code, 200)
        return reponse.text

    def _csrf(self):
        """Le jeton CSRF de la session, pris sur la page : les essais de refus
        mesurent la GARDE, pas la protection CSRF.

        ⚠️ Pas seulement dans le formulaire du bouton : une page sans bouton ni
        pièce attendue ne porte aucun `<input csrf_token>`, et c'est justement
        l'état de la moitié des essais de refus. Le gabarit public d'Odoo pose
        toujours le jeton dans son script d'en-tête.
        """
        page = self._page()
        trouve = JETON_CSRF.search(page) or JETON_CSRF_SCRIPT.search(page)
        self.assertTrue(trouve, "aucun jeton CSRF sur la page")
        return trouve.group(1)

    def _cliquer(self, project_id=None, jeton=None, csrf=None):
        """Le geste du bouton : un POST, comme le formulaire de la page."""
        return self.url_open(
            "/progression/%s/%s/questionnaire" % (
                project_id if project_id is not None else self.projet.id,
                jeton if jeton is not None else self.jeton),
            data={"csrf_token": csrf or self._csrf()}, allow_redirects=False)

    def _reponses(self, sondage=None):
        return self.Reponses.search([("survey_id", "=", (sondage or self.sondage).id)])

    # ------------------------------------------------------------------
    # Le bouton
    # ------------------------------------------------------------------

    def test_sans_questionnaire_rien_du_tout_sur_la_page(self):
        """Le bloc est ABSENT, pas vide. ⚠️ Mesurer la seule absence du libellé
        laissait passer un `<div>` vide, posé sur des pages en service."""
        page = self._page()
        self.assertNotIn(BOUTON, page)
        self.assertNotIn("o_progression_portal_survey", page)

    def test_une_etape_sans_rien_a_ouvrir_ne_pose_pas_de_bloc(self):
        """Même mesure du côté de l'étape : questionnaire archivé, donc rien à
        ouvrir, donc aucun bloc dans le bloc de l'étape."""
        self.projet.write({
            "progression_survey_id": self.sondage.id,
            "progression_survey_stage_id": self.colonnes[1].id,
        })
        self.sondage.active = False
        self.assertNotIn("o_progression_portal_survey", self._page())

    def test_sans_etape_le_bouton_sort_une_seule_fois(self):
        self.projet.progression_survey_id = self.sondage
        self.assertEqual(self._page().count(BOUTON), 1)

    def test_avec_une_etape_le_bouton_sort_dans_cette_etape(self):
        """L'étape 2 est l'étape en cours du mandat d'essai."""
        self.projet.write({
            "progression_survey_id": self.sondage.id,
            "progression_survey_stage_id": self.colonnes[1].id,
        })
        page = self._page()
        self.assertEqual(page.count(BOUTON), 1)
        # Dans le bloc de SON étape, et nulle part ailleurs. ⚠️ Comparer des
        # positions de texte ne suffit pas : `t-out` échappe l'apostrophe de
        # « Signature de l'acte » en `&#39;`.
        blocs = page.split("o_progression_portal_step o_progression_portal_step_")
        porteurs = [i for i, bloc in enumerate(blocs[1:], 1) if BOUTON in bloc]
        self.assertEqual(len(porteurs), 1)
        self.assertIn("Pièces du client", blocs[porteurs[0]])

    def test_une_etape_invisible_au_client_ne_montre_rien(self):
        """La colonne 3 est interne : son étape n'est pas sur la page, et la
        route la refuse aussi — le README le promet, et seul le cas « étape à
        venir » le mesurait."""
        self.projet.write({
            "progression_survey_id": self.sondage.id,
            "progression_survey_stage_id": self.colonnes[2].id,
        })
        self.assertNotIn(BOUTON, self._page())
        self.assertEqual(self._cliquer().status_code, 404)
        self.assertFalse(self._reponses())

    def test_un_questionnaire_archive_ne_laisse_pas_de_bouton(self):
        self.projet.progression_survey_id = self.sondage
        self.sondage.active = False
        self.assertNotIn(BOUTON, self._page())
        self.assertEqual(self._cliquer().status_code, 404)
        self.assertFalse(self._reponses())

    def test_le_bouton_parle_la_langue_du_client(self):
        """Le catalogue d'un module neuf se prouve en base, pas dans son fichier :
        un msgid absent de son .pot est jeté en silence au chargement."""
        self.env["res.lang"]._activate_lang("fr_CA")
        self.env["res.lang"]._activate_lang("en_US")
        self.env["ir.module.module"]._load_module_terms(["bf_stepbystep_survey"], ["fr_CA"])
        self.projet.progression_survey_id = self.sondage
        self.projet.partner_id.lang = "fr_CA"
        self.assertIn("Remplir le questionnaire", self._page())
        self.projet.partner_id.lang = "en_US"
        self.assertIn(BOUTON, self._page())

    # ------------------------------------------------------------------
    # La réponse
    # ------------------------------------------------------------------

    def test_le_geste_cree_une_seule_reponse_rattachee_au_mandat(self):
        self.projet.progression_survey_id = self.sondage
        reponse = self._cliquer()
        self.assertEqual(reponse.status_code, 303)
        reponses = self._reponses()
        self.assertEqual(len(reponses), 1)
        self.assertEqual(self.projet.progression_survey_answer_id, reponses)
        self.assertEqual(reponses.partner_id, self.projet.partner_id)
        self.assertIn("/survey/%s/%s" % (self.sondage.access_token, reponses.access_token),
                      reponse.headers["Location"])
        # Deuxième geste : la même réponse, jamais deux copies à moitié remplies.
        self._cliquer()
        self.assertEqual(self._reponses(), reponses)

    def test_la_reponse_est_nommee_a_celui_que_la_page_nomme(self):
        """🔴 La réponse suit `portal_client`, la valeur que la PAGE affiche, et
        non le `partner_id` brut du projet : le bénéficiaire quand un pont le
        pose, et la société mère quand le mandat porte
        un contact. Un mandat dont le client est un contact fils doit nommer la
        société, comme la page le fait.
        """
        societe = self.env["res.partner"].create({"name": "Groupe du mandat"})
        contact = self.env["res.partner"].create({
            "name": "Contact du dossier", "parent_id": societe.id})
        self.projet.write({
            "partner_id": contact.id,
            "progression_survey_id": self.sondage.id,
        })
        self.assertEqual(self._cliquer().status_code, 303)
        reponse = self.projet.progression_survey_answer_id
        self.assertEqual(reponse.partner_id, societe)
        self.assertNotEqual(reponse.partner_id, contact)
        # Et la page nomme la même partie.
        self.assertIn("Groupe du mandat", self._page())

    def test_deux_mandats_du_meme_payeur_ne_partagent_pas_leur_reponse(self):
        """🔴 La forme ordinaire chez un donneur d'ordre : il paie pour deux
        bénéficiaires, et leurs pages publiées portent le
        MÊME `partner_id`. Une clé par client ferait lire au second la réponse du
        premier, et la lui ferait écraser."""
        autre, _colonnes = self._mandat(self.env)
        autre.partner_id = self.projet.partner_id          # le même payeur
        autre.action_progression_portal_publish()
        self.projet.progression_survey_id = self.sondage
        autre.progression_survey_id = self.sondage          # le même questionnaire

        self._cliquer()
        self._cliquer(project_id=autre.id, jeton=autre.progression_portal_token)

        self.assertEqual(len(self._reponses()), 2)
        self.assertTrue(self.projet.progression_survey_answer_id)
        self.assertTrue(autre.progression_survey_answer_id)
        self.assertNotEqual(self.projet.progression_survey_answer_id,
                            autre.progression_survey_answer_id)
        # Et la page du second ne dit pas « reçu » parce que le premier a fini.
        self.projet.progression_survey_answer_id.state = "done"
        self.assertNotIn(RECU, self._page(projet=autre))
        self.assertIn(BOUTON, self._page(projet=autre))

    def test_un_mandat_sans_client_ne_sert_pas_une_reponse_anonyme_d_un_autre(self):
        """🔴 `partner_id` est facultatif sur un mandat ET sur une réponse : une
        recherche sur « partenaire vide » attrapait toutes les réponses anonymes
        du questionnaire, celles d'étrangers comprises."""
        etrangere = self.sondage.sudo()._create_answer(check_attempts=False)
        self.projet.write({"partner_id": False, "progression_survey_id": self.sondage.id})
        self.assertEqual(self._cliquer().status_code, 303)
        self.assertNotEqual(self.projet.progression_survey_answer_id, etrangere)
        self.assertTrue(self.projet.progression_survey_answer_id)
        self.assertEqual(len(self._reponses()), 2)

    def test_un_geste_concurrent_est_rejoue(self):
        """🔴 La protection contre deux gestes simultanés tient au REJEU d'Odoo,
        donc à la REMONTÉE de l'erreur de sérialisation. Avalée par le filet de
        la route, elle rendait 404 au client sur un double-clic légitime, et le
        verrou du modèle ne corrigeait plus rien.

        L'essai simule la course perdue : la première tentative lève ce que
        PostgreSQL lèverait, la seconde doit aboutir.
        """
        self.projet.progression_survey_id = self.sondage
        Projet = type(self.env["project.project"])
        vrai = Projet._progression_survey_ouvrir
        appels = []

        def faux(projet, partenaire=None):
            appels.append(1)
            if len(appels) == 1:
                raise CourseSimulee("course simulée")
            return vrai(projet, partenaire=partenaire)

        with patch.object(Projet, "_progression_survey_ouvrir", faux):
            reponse = self._cliquer()
        self.assertEqual(len(appels), 2, "la requête n'a pas été rejouée")
        self.assertEqual(reponse.status_code, 303)
        self.assertEqual(len(self._reponses()), 1)

    def test_une_reponse_finie_ne_se_rouvre_pas(self):
        """Le bouton disparaît ET la route refuse : le jeton d'une réponse finie
        n'a plus à voyager dans une adresse, un signet ou un journal de proxy."""
        self.projet.progression_survey_id = self.sondage
        self._cliquer()
        reponse = self.projet.progression_survey_answer_id
        reponse.state = "done"
        page = self._page()
        self.assertNotIn(BOUTON, page)
        self.assertIn(RECU, page)
        # Un bouton resté ouvert dans un autre onglet ramène à la page, qui dit
        # « reçu » — et l'adresse ne porte que le jeton de la PAGE.
        geste = self._cliquer()
        self.assertEqual(geste.status_code, 303)
        self.assertIn("/progression/%s/%s" % (self.projet.id, self.jeton), geste.headers["Location"])
        self.assertNotIn(reponse.access_token, geste.headers["Location"])
        self.assertEqual(self._reponses(), reponse)

    # ------------------------------------------------------------------
    # La reprise de la réponse déjà envoyée
    # ------------------------------------------------------------------

    def _deja_envoyee(self, societe=None, sondage=None, **valeurs):
        """La réponse nominative qu'un lien de courriel a créée pour un contact
        de l'entreprise, avant tout geste sur la page."""
        societe = societe or self.projet.partner_id.commercial_partner_id
        contact = self.env["res.partner"].create({
            "name": "Contact de %s" % societe.name, "parent_id": societe.id})
        reponse = (sondage or self.sondage).sudo()._create_answer(
            partner=contact, check_attempts=False)
        if valeurs:
            reponse.write(valeurs)
        return reponse

    def test_le_geste_reprend_la_reponse_deja_envoyee(self):
        """🔴 Le cas d'origine : l'entreprise a reçu son lien par
        courriel. Le bouton doit mener à CETTE réponse, pas en créer une seconde."""
        envoyee = self._deja_envoyee()
        self.projet.progression_survey_id = self.sondage
        geste = self._cliquer()
        self.assertEqual(geste.status_code, 303)
        self.assertIn("/survey/%s/%s" % (self.sondage.access_token, envoyee.access_token),
                      geste.headers["Location"])
        self.assertEqual(self.projet.progression_survey_answer_id, envoyee)
        self.assertEqual(self._reponses(), envoyee)

    def test_une_reponse_deja_finie_est_reprise_et_la_page_dit_recu(self):
        envoyee = self._deja_envoyee(state="done")
        self.projet.progression_survey_id = self.sondage
        self.assertIn(BOUTON, self._page())
        geste = self._cliquer()
        self.assertEqual(geste.status_code, 303)
        self.assertIn("/progression/%s/%s" % (self.projet.id, self.jeton), geste.headers["Location"])
        self.assertNotIn(envoyee.access_token, geste.headers["Location"])
        self.assertEqual(self.projet.progression_survey_answer_id, envoyee)
        self.assertEqual(self._reponses(), envoyee)
        page = self._page()
        self.assertIn(RECU, page)
        self.assertNotIn(BOUTON, page)

    def test_la_plus_avancee_est_reprise(self):
        self._deja_envoyee()
        commencee = self._deja_envoyee(state="in_progress")
        self._deja_envoyee()
        self.projet.progression_survey_id = self.sondage
        self._cliquer()
        self.assertEqual(self.projet.progression_survey_answer_id, commencee)

    def test_la_finie_passe_avant_la_commencee_puis_la_plus_recente(self):
        """L'ordre de préférence, avec une réponse finie et deux réponses au
        même état."""
        self._deja_envoyee(state="in_progress")
        finie = self._deja_envoyee(state="done")
        self._deja_envoyee(state="in_progress")
        self.projet.progression_survey_id = self.sondage
        self._cliquer()
        self.assertEqual(self.projet.progression_survey_answer_id, finie)
        # À état égal, la plus récente.
        autre, _colonnes = self._mandat(self.env)
        autre.action_progression_portal_publish()
        ancienne = self._deja_envoyee(societe=autre.partner_id, state="in_progress")
        recente = self._deja_envoyee(societe=autre.partner_id, state="in_progress")
        self.assertGreater(recente.id, ancienne.id)
        autre.progression_survey_id = self.sondage
        self._cliquer(project_id=autre.id, jeton=autre.progression_portal_token)
        self.assertEqual(autre.progression_survey_answer_id, recente)

    def test_la_reponse_d_un_autre_mandat_n_est_pas_reprise(self):
        """Même archivé, un mandat garde sa réponse : la reprendre la ferait lire
        et écraser par la page d'un autre mandat."""
        envoyee = self._deja_envoyee()
        autre, _colonnes = self._mandat(self.env)
        autre.write({"progression_survey_id": self.sondage.id,
                     "progression_survey_answer_id": envoyee.id, "active": False})
        self.projet.progression_survey_id = self.sondage
        self._cliquer()
        self.assertTrue(self.projet.progression_survey_answer_id)
        self.assertNotEqual(self.projet.progression_survey_answer_id, envoyee)

    def test_un_essai_ou_une_autre_entreprise_n_est_pas_repris(self):
        essai = self._deja_envoyee(test_entry=True)
        ailleurs = self._deja_envoyee(societe=self.env["res.partner"].create({"name": "Voisin inc."}))
        self.projet.progression_survey_id = self.sondage
        self._cliquer()
        self.assertNotIn(self.projet.progression_survey_answer_id, essai | ailleurs)
        self.assertEqual(len(self._reponses()), 3)

    def test_un_autre_questionnaire_n_est_pas_repris(self):
        autre_sondage = self._questionnaire(self.env, title="Satisfaction")
        self._deja_envoyee(sondage=autre_sondage)
        self.projet.progression_survey_id = self.sondage
        self._cliquer()
        self.assertEqual(self.projet.progression_survey_answer_id.survey_id, self.sondage)
        self.assertEqual(len(self._reponses()), 1)

    def test_le_payeur_de_plusieurs_mandats_ne_reprend_rien(self):
        """🔴 Un donneur d'ordre sans bénéficiaire nommé : sa page nomme le
        payeur, pas le client qui la lit. La réponse d'un contact du payeur ne
        doit pas lui être servie."""
        envoyee = self._deja_envoyee()
        autre, _colonnes = self._mandat(self.env)
        autre.write({"partner_id": self.projet.partner_id.id,
                     "progression_survey_id": self.sondage.id})
        self.projet.progression_survey_id = self.sondage
        self._cliquer()
        self.assertTrue(self.projet.progression_survey_answer_id)
        self.assertNotEqual(self.projet.progression_survey_answer_id, envoyee)
        self.assertEqual(len(self._reponses()), 2)

    def _vieillir(self, reponse, jours):
        """Recule la création d'une réponse : dans un essai, mandat et réponse
        naissent dans la même transaction, donc à la même heure."""
        self.env.cr.execute(
            "UPDATE survey_user_input SET create_date = create_date - %s * interval '1 day' "
            "WHERE id = %s", (jours, reponse.id))
        reponse.invalidate_recordset(["create_date"])

    def test_une_reponse_anterieure_au_mandat_n_est_pas_reprise(self):
        """🔴 Une réponse finie de l'an dernier, que
        personne ne porte, faisait dire « reçu » au mandat de cette année et
        laissait l'invitation du jour orpheline."""
        vieille = self._deja_envoyee(state="done")
        self._vieillir(vieille, 365)
        fraiche = self._deja_envoyee()
        self.projet.progression_survey_id = self.sondage
        geste = self._cliquer()
        self.assertEqual(self.projet.progression_survey_answer_id, fraiche)
        self.assertIn(fraiche.access_token, geste.headers["Location"])
        self.assertIn(BOUTON, self._page())

    def test_le_contact_parti_ailleurs_avant_le_mandat_n_emporte_pas_sa_reponse(self):
        """🔴 Jean a commencé le questionnaire chez Alpha, puis passe chez
        Bêta. Sa réponse, antérieure au mandat de Bêta, ne lui est pas
        servie quand elle sort de la fenêtre de reprise. (Un changement
        d'entreprise DANS la fenêtre reste hors de portée : la note au chatter
        et « Détacher » en sont le recours.)"""
        Partner = self.env["res.partner"]
        alpha = Partner.create({"name": "Alpha Essai inc.", "is_company": True})
        beta = Partner.create({"name": "Bêta Essai inc.", "is_company": True})
        jean = Partner.create({"name": "Jean Fictif", "parent_id": alpha.id})
        chez_alpha = self.sondage.sudo()._create_answer(partner=jean, check_attempts=False)
        chez_alpha.state = "in_progress"
        self._vieillir(chez_alpha, 120)
        jean.parent_id = beta
        self.projet.write({"partner_id": beta.id, "progression_survey_id": self.sondage.id})
        geste = self._cliquer()
        self.assertNotIn(chez_alpha.access_token, geste.headers["Location"])
        self.assertNotEqual(self.projet.progression_survey_answer_id, chez_alpha)

    def test_une_invitation_envoyee_avant_le_mandat_est_reprise(self):
        """🔴 Le cas courant : l'invitation part, l'entreprise répond, et le
        mandat n'est créé que quelques jours plus tard."""
        envoyee = self._deja_envoyee(state="done")
        self._vieillir(envoyee, 20)
        self.projet.progression_survey_id = self.sondage
        self._cliquer()
        self.assertEqual(self.projet.progression_survey_answer_id, envoyee)
        self.assertIn(RECU, self._page())

    def test_la_fenetre_compte_depuis_le_clic_meme_sur_un_vieux_mandat(self):
        """Un mandat ouvert depuis 400 jours ne rend pas éligible une
        réponse de 300 jours ; une réponse de 20 jours l'est."""
        self.env.cr.execute(
            "UPDATE project_project SET create_date = create_date - interval '400 days' WHERE id = %s",
            (self.projet.id,))
        self.projet.invalidate_recordset(["create_date"])
        vieille = self._deja_envoyee(state="done")
        self._vieillir(vieille, 300)
        recente = self._deja_envoyee(state="in_progress")
        self._vieillir(recente, 20)
        self.projet.progression_survey_id = self.sondage
        self._cliquer()
        self.assertEqual(self.projet.progression_survey_answer_id, recente)

    def test_l_echeance_tombe_a_la_reprise(self):
        """N3 : une invitation qui échoit demain, reprise aujourd'hui, ne rend pas
        le bouton mort la semaine prochaine."""
        envoyee = self._deja_envoyee(deadline=fields.Datetime.now() + timedelta(days=1))
        self.projet.progression_survey_id = self.sondage
        self._cliquer()
        self.assertEqual(self.projet.progression_survey_answer_id, envoyee)
        self.assertFalse(envoyee.deadline)

    def test_un_client_direct_avec_un_autre_projet_retrouve_sa_reponse(self):
        """Un client qui paie lui-même ce mandat ET une banque d'heures
        n'est pas un donneur d'ordre. Exige le pont du bénéficiaire (sans lui,
        tout autre mandat du payeur suffit à bloquer, par prudence)."""
        if "bf_beneficiary_id" not in self.env["project.project"]._fields:
            self.skipTest("pont du bénéficiaire absent")
        banque, _colonnes = self._mandat(self.env)
        banque.write({"name": "Banque d'heures", "partner_id": self.projet.partner_id.id})
        envoyee = self._deja_envoyee()
        self.projet.progression_survey_id = self.sondage
        self._cliquer()
        self.assertEqual(self.projet.progression_survey_answer_id, envoyee)

    def test_un_beneficiaire_qui_paie_ailleurs_retrouve_sa_reponse(self):
        """L'autre forme : la page nomme Alpha, bénéficiaire ; qu'Alpha paie
        aussi un autre projet ne rend pas sa page ambiguë."""
        if "bf_beneficiary_id" not in self.env["project.project"]._fields:
            self.skipTest("pont du bénéficiaire absent")
        alpha = self.env["res.partner"].create({"name": "Alpha Essai inc.", "is_company": True})
        # Alpha paie elle-même un mandat pour un tiers, au même questionnaire :
        # c'est exactement ce que la garde du payeur guette.
        propre, _colonnes = self._mandat(self.env)
        propre.write({"partner_id": alpha.id, "progression_survey_id": self.sondage.id,
                      "bf_beneficiary_id": self.env["res.partner"].create({"name": "Tiers inc."}).id})
        self.projet.write({"bf_beneficiary_id": alpha.id, "progression_survey_id": self.sondage.id})
        envoyee = self._deja_envoyee(societe=alpha)
        self._cliquer()
        self.assertEqual(self.projet.progression_survey_answer_id, envoyee)

    def test_une_invitation_echue_n_est_pas_reprise(self):
        """🔴 B2 : une réponse dont l'échéance est passée rendait le bouton mort
        pour toujours (« survey expired » à chaque clic)."""
        echue = self._deja_envoyee(deadline=fields.Datetime.now() - timedelta(days=1))
        self.projet.progression_survey_id = self.sondage
        self._cliquer()
        self.assertTrue(self.projet.progression_survey_answer_id)
        self.assertNotEqual(self.projet.progression_survey_answer_id, echue)

    def test_une_reponse_hors_delai_n_est_pas_reprise(self):
        """C1 : ouverte il y a une semaine sur un questionnaire de 30 minutes,
        elle se fermait à moitié vide au premier affichage."""
        self.sondage.write({"is_time_limited": True, "time_limit": 30})
        hors_delai = self._deja_envoyee(
            state="in_progress",
            start_datetime=fields.Datetime.now() - timedelta(days=7))
        self.assertTrue(hors_delai.survey_time_limit_reached)
        self.projet.progression_survey_id = self.sondage
        self._cliquer()
        self.assertNotEqual(self.projet.progression_survey_answer_id, hors_delai)
        self.assertEqual(hors_delai.state, "in_progress")

    def test_le_payeur_d_un_autre_mandat_sans_questionnaire_ne_reprend_rien(self):
        """C2 : la garde regardait seulement les mandats qui portaient DÉJÀ ce
        questionnaire. Un autre mandat du même payeur, au nom d'un de ses
        CONTACTS et sans questionnaire, suffit maintenant."""
        envoyee = self._deja_envoyee()
        contact_du_payeur = self.env["res.partner"].create({
            "name": "Comptabilité du payeur",
            "parent_id": self.projet.partner_id.commercial_partner_id.id})
        autre, _colonnes = self._mandat(self.env)
        autre.write({"partner_id": contact_du_payeur.id, "active": False})
        if "bf_beneficiary_id" in autre._fields:
            # Avec le pont, c'est le bénéficiaire nommé sur l'AUTRE mandat qui
            # trahit le donneur d'ordre.
            autre.bf_beneficiary_id = self.env["res.partner"].create({"name": "Bénéficiaire inc."})
        self.projet.progression_survey_id = self.sondage
        self._cliquer()
        self.assertNotEqual(self.projet.progression_survey_answer_id, envoyee)
        self.assertEqual(len(self._reponses()), 2)

    def test_la_reprise_laisse_une_note_au_mandat(self):
        envoyee = self._deja_envoyee()
        self.projet.progression_survey_id = self.sondage
        self._cliquer()
        self.assertEqual(self.projet.progression_survey_answer_id, envoyee)
        notes = self.projet.message_ids.filtered(
            lambda n: "took over" in (n.body or "") or "repris" in (n.body or ""))
        self.assertEqual(len(notes), 1)
        self.assertIn(envoyee.partner_id.name, notes.body)
        self.assertTrue(notes.subtype_id.internal)

    def test_detacher_la_reponse_la_retire_pour_de_bon(self):
        """N4 : une seconde candidate n'est PAS reprise après un détachement —
        la personne a dit « pas celle-là », la page repart de zéro."""
        seconde = self._deja_envoyee(state="done")
        envoyee = self._deja_envoyee(state="done")
        self.projet.progression_survey_id = self.sondage
        self._cliquer()
        self.assertEqual(self.projet.progression_survey_answer_id, envoyee)
        self.projet.action_progression_survey_detacher()
        self.assertFalse(self.projet.progression_survey_answer_id)
        self.assertTrue(envoyee.exists())
        # Le clic suivant n'y revient pas : une réponse neuve.
        geste = self._cliquer()
        self.assertNotIn(envoyee.access_token, geste.headers["Location"])
        self.assertTrue(self.projet.progression_survey_answer_id)
        self.assertNotIn(self.projet.progression_survey_answer_id, envoyee | seconde)
        self.assertIn(BOUTON, self._page())

    def test_seul_l_administrateur_des_projets_detache(self):
        envoyee = self._deja_envoyee()
        self.projet.write({"progression_survey_id": self.sondage.id,
                           "progression_survey_answer_id": envoyee.id})
        usager = self.env["res.users"].create({
            "name": "Usager des projets", "login": "usager_28063",
            "groups_id": [(6, 0, [self.env.ref("project.group_project_user").id])]})
        with self.assertRaises(AccessError):
            self.projet.with_user(usager).action_progression_survey_detacher()
        self.assertEqual(self.projet.progression_survey_answer_id, envoyee)

    def test_le_beneficiaire_nomme_retrouve_sa_reponse(self):
        """La forme courante : un organisme paie deux mandats, chacun nomme son
        bénéficiaire, et la page d'Alpha reprend la réponse d'Alpha — pas celle de
        Bêta. Exige le pont du bénéficiaire ; sauté sans lui."""
        if "bf_beneficiary_id" not in self.env["project.project"]._fields:
            self.skipTest("pont du bénéficiaire absent")
        payeur = self.projet.partner_id
        alpha = self.env["res.partner"].create({"name": "Alpha Essai inc.", "is_company": True})
        beta = self.env["res.partner"].create({"name": "Bêta Essai inc.", "is_company": True})
        autre, _colonnes = self._mandat(self.env)
        autre.partner_id = payeur
        autre.action_progression_portal_publish()
        self.projet.write({"bf_beneficiary_id": alpha.id, "progression_survey_id": self.sondage.id})
        autre.write({"bf_beneficiary_id": beta.id, "progression_survey_id": self.sondage.id})
        a_alpha = self._deja_envoyee(societe=alpha)
        a_beta = self._deja_envoyee(societe=beta)
        self._cliquer()
        self._cliquer(project_id=autre.id, jeton=autre.progression_portal_token)
        self.assertEqual(self.projet.progression_survey_answer_id, a_alpha)
        self.assertEqual(autre.progression_survey_answer_id, a_beta)
        self.assertEqual(len(self._reponses()), 2)

    def test_changer_de_questionnaire_detache_l_ancienne_reponse(self):
        self.projet.progression_survey_id = self.sondage
        self._cliquer()
        ancienne = self.projet.progression_survey_answer_id
        neuf = self._questionnaire(self.env, title="Diagnostic 20 h")
        self.projet.progression_survey_id = neuf
        self.assertIn(BOUTON, self._page())
        self._cliquer()
        self.assertNotEqual(self.projet.progression_survey_answer_id, ancienne)
        self.assertEqual(self.projet.progression_survey_answer_id.survey_id, neuf)

    def test_la_copie_d_un_mandat_ne_garde_pas_la_reponse(self):
        """Sinon le mandat copié naît « déjà répondu », avec la réponse de l'autre."""
        self.projet.progression_survey_id = self.sondage
        self._cliquer()
        copie = self.projet.copy()
        self.assertEqual(copie.progression_survey_id, self.sondage)
        self.assertFalse(copie.progression_survey_answer_id)

    # ------------------------------------------------------------------
    # La garde
    # ------------------------------------------------------------------

    def test_la_route_refuse_ce_que_la_page_refuse(self):
        self.projet.progression_survey_id = self.sondage
        autre, _colonnes = self._mandat(self.env)
        autre.action_progression_portal_publish()
        csrf = self._csrf()
        cas = {
            "jeton faux": (self.projet.id, "x" * 43),
            "jeton d'un autre projet": (self.projet.id, autre.progression_portal_token),
            "projet absent": (999999999, self.jeton),
            "jeton non ASCII": (self.projet.id, "%C3%A9"),
            "jeton trop long": (self.projet.id, "x" * 4000),
        }
        for nom, (pid, jeton) in cas.items():
            with self.subTest(nom):
                self.assertEqual(
                    self._cliquer(project_id=pid, jeton=jeton, csrf=csrf).status_code, 404)
        self.assertFalse(self._reponses())

        self.projet.action_progression_portal_unpublish()
        self.assertEqual(self._cliquer(csrf=csrf).status_code, 404)
        self.projet.action_progression_portal_publish()

        archive, _ca = self._mandat(self.env)
        archive.action_progression_portal_publish()
        archive.progression_survey_id = self.sondage
        jeton_archive = archive.progression_portal_token
        archive.active = False
        self.assertEqual(
            self._cliquer(project_id=archive.id, jeton=jeton_archive, csrf=csrf).status_code,
            404)
        self.assertFalse(self._reponses())

    def test_la_route_se_ferme_quand_le_mandat_demande_un_code(self):
        """🔴 La garde de la double authentification vit dans
        `_progression_projet` du cœur, que cette route appelle sans rien en
        savoir : la promesse « tout chemin la subit » se mesure ICI, sur un pont,
        pas seulement sur le dépôt du cœur.

        ⚠️ Exige le cœur 18.0.2.7.0 (le champ `progression_otp_required`).
        """
        self.projet.progression_survey_id = self.sondage
        csrf = self._csrf()
        self.projet.progression_otp_required = True
        self.assertEqual(self._cliquer(csrf=csrf).status_code, 404)
        self.assertFalse(self._reponses())

    def test_une_etape_a_venir_ne_s_ouvre_pas_d_avance(self):
        """La règle du dépôt de pièces : le client n'ouvre pas ce qu'on lui
        demandera plus tard. L'étape 4 du mandat d'essai est à venir."""
        self.projet.write({
            "progression_survey_id": self.sondage.id,
            "progression_survey_stage_id": self.colonnes[3].id,
        })
        self.assertNotIn(BOUTON, self._page())
        self.assertEqual(self._cliquer().status_code, 404)
        self.assertFalse(self._reponses())

    def test_le_geste_sans_jeton_csrf_est_refuse(self):
        self.projet.progression_survey_id = self.sondage
        # ⚠️ Un corps VIDE n'est pas un POST : `requests` retombe sur un GET, et
        # l'essai mesurait le 405 de la méthode au lieu du refus CSRF.
        reponse = self.url_open(
            "/progression/%s/%s/questionnaire" % (self.projet.id, self.jeton),
            data={"csrf_token": "faux"}, allow_redirects=False)
        self.assertEqual(reponse.status_code, 400)
        self.assertFalse(self._reponses())

    def test_un_GET_ne_cree_rien(self):
        """La route est en POST : un préchargeur de liens, un antivirus de
        courriel ou un robot qui suit l'adresse ne doit rien créer."""
        self.projet.progression_survey_id = self.sondage
        reponse = self.url_open(
            "/progression/%s/%s/questionnaire" % (self.projet.id, self.jeton),
            allow_redirects=False)
        self.assertEqual(reponse.status_code, 405)
        self.assertFalse(self._reponses())


@tagged("post_install", "-at_install")
class TestGardesDeLaFiche(TransactionCase, _AvecQuestionnaire):

    def test_une_etape_d_un_autre_projet_est_refusee(self):
        projet, _colonnes = self._mandat(self.env)
        _autre, colonnes_autre = self._mandat(self.env)
        with self.assertRaises(ValidationError):
            projet.progression_survey_stage_id = colonnes_autre[0]

    def test_retirer_l_etape_du_projet_est_refuse(self):
        """⚠️ La contrainte surveille `type_ids` aussi : sinon l'étape devenait
        étrangère après coup et le bouton disparaissait en silence."""
        projet, colonnes = self._mandat(self.env)
        projet.progression_survey_stage_id = colonnes[1]
        with self.assertRaises(ValidationError):
            projet.type_ids = colonnes - colonnes[1]

    def test_un_questionnaire_qui_exige_un_compte_est_refuse(self):
        """Le client n'a pas de compte : c'est tout le propos de la page."""
        projet, _colonnes = self._mandat(self.env)
        sondage = self._questionnaire(self.env, users_login_required=True)
        with self.assertRaises(ValidationError):
            projet.progression_survey_id = sondage

    def test_cocher_require_login_apres_coup_ferme_le_bouton(self):
        """La contrainte ne peut rien contre une écriture sur le questionnaire :
        l'état se relit donc au rendu."""
        projet, _colonnes = self._mandat(self.env)
        sondage = self._questionnaire(self.env)
        projet.progression_survey_id = sondage
        self.assertEqual(projet._progression_survey_etat(), "ouvert")
        sondage.users_login_required = True
        self.assertEqual(projet._progression_survey_etat(), "ferme")

    def test_sans_questionnaire_rien_a_ouvrir(self):
        projet, _colonnes = self._mandat(self.env)
        self.assertEqual(projet._progression_survey_etat(), "ferme")
        self.assertFalse(projet._progression_survey_ouvrir())
        self.assertFalse(projet._progression_survey_reponse())
