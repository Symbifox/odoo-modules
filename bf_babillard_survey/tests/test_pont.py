# -*- coding: utf-8 -*-
"""Essais du pont Babillard ↔ Sondages Odoo."""
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, tagged


class PontCommun(TransactionCase):
    """Le socle des deux jeux d'essais : la rédactrice, la personne ordinaire.

    ⚠️ Une classe à part, et non un héritage entre les deux jeux : un jeu
    d'essais qui hérite d'un autre REJOUE tous ses essais, et le compte annoncé
    double sans que rien de plus soit mesuré.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Users = cls.env["res.users"].with_context(no_reset_password=True)
        cls.g_interne = cls.env.ref("base.group_user")
        cls.g_redaction = cls.env.ref("bf_babillard.group_babillard_redacteur")
        cls.u_redactrice = Users.create({
            "name": "Rédactrice du pont", "login": "pont_survey_redactrice",
            "email": "pont.survey@exemple.test",
            "groups_id": [(6, 0, [cls.g_interne.id, cls.g_redaction.id,
                                  cls.env.ref("survey.group_survey_user").id])]})
        cls.u_simple = Users.create({
            "name": "Personne ordinaire", "login": "pont_survey_simple",
            "email": "pont.survey.simple@exemple.test",
            "groups_id": [(6, 0, [cls.g_interne.id,
                                  cls.env.ref("survey.group_survey_user").id])]})

    def _sondage(self, **kw):
        vals = {"title": "Ce qu'on garde du 5 à 7", "access_mode": "public"}
        vals.update(kw)
        return self.env["survey.survey"].with_user(self.u_redactrice).create(vals)

    def _carte(self, sondage):
        return self.env["bf.babillard.post"].sudo().with_context(
            active_test=False).search([
                ("source_model", "=", "survey.survey"),
                ("source_res_id", "=", sondage.id)])


@tagged("post_install", "-at_install")
class TestPontSondages(PontCommun):

    def test_annonce_pose_une_carte_avec_le_lien(self):
        sondage = self._sondage()
        sondage.with_user(self.u_redactrice).action_annoncer_au_babillard()
        carte = self._carte(sondage)
        self.assertEqual(len(carte), 1)
        self.assertEqual(carte.name, sondage.title)
        self.assertEqual(carte.state, "publie")
        self.assertIn(sondage.access_token, carte.corps_html)

    def test_annoncer_deux_fois_ne_pose_quune_carte(self):
        sondage = self._sondage()
        sondage.with_user(self.u_redactrice).action_annoncer_au_babillard()
        sondage.with_user(self.u_redactrice).action_annoncer_au_babillard()
        self.assertEqual(len(self._carte(sondage)), 1)

    def test_sondage_sur_invitation_refuse(self):
        """🔴 Le lien de départ d'un sondage à jeton ne mène nulle part sans
        l'invitation nominative : une carte lue par toute la maison ne peut pas
        le porter."""
        sondage = self._sondage(access_mode="token")
        with self.assertRaises(UserError):
            sondage.with_user(self.u_redactrice).action_annoncer_au_babillard()
        self.assertFalse(self._carte(sondage))

    def test_sondage_archive_refuse(self):
        sondage = self._sondage()
        sondage.sudo().active = False
        with self.assertRaises(UserError):
            sondage.with_user(self.u_redactrice).action_annoncer_au_babillard()

    def test_sans_la_redaction_personne_ne_publie(self):
        """🔴 La méthode est publique : le `groups=` du bouton ne garde que
        l'écran."""
        sondage = self._sondage()
        with self.assertRaises(AccessError):
            sondage.with_user(self.u_simple).action_annoncer_au_babillard()
        self.assertFalse(self._carte(sondage))


@tagged("post_install", "-at_install")
class TestPontSeanceEnDirect(PontCommun):
    """La séance OUVERTE annoncée au fil, avec son code."""

    def _seance(self, **kw):
        """Un sondage avec une question et une séance ouverte.

        ⚠️ Le cœur refuse d'ouvrir une séance sur un sondage sans question, et
        `session_code` ne vaut quelque chose qu'une fois la séance ouverte.
        """
        sondage = self._sondage(survey_type="live_session", **kw)
        self.env["survey.question"].sudo().create({
            "survey_id": sondage.id, "title": "Quelle couleur", "sequence": 1,
            "question_type": "simple_choice",
            "suggested_answer_ids": [(0, 0, {"value": "Bleu"})],
        })
        sondage.sudo().action_start_session()
        return sondage

    def test_la_carte_de_la_seance_porte_le_code_et_le_lien_court(self):
        sondage = self._seance()
        sondage.with_user(self.u_redactrice).action_annoncer_seance_au_babillard()
        carte = self._carte(sondage)
        self.assertEqual(len(carte), 1)
        self.assertEqual(carte.state, "publie")
        self.assertIn(sondage.session_code, carte.corps_html)
        self.assertIn("/s/%s" % sondage.session_code, carte.corps_html)

    def test_sans_seance_ouverte_il_n_y_a_pas_de_code_a_annoncer(self):
        sondage = self._sondage(survey_type="live_session")
        with self.assertRaises(UserError):
            sondage.with_user(self.u_redactrice).action_annoncer_seance_au_babillard()
        self.assertFalse(self._carte(sondage))

    def test_une_seance_close_ne_s_annonce_plus(self):
        sondage = self._seance()
        sondage.sudo().action_end_session()
        with self.assertRaises(UserError):
            sondage.with_user(self.u_redactrice).action_annoncer_seance_au_babillard()

    def test_la_deuxieme_seance_reecrit_la_carte_avec_le_code_du_jour(self):
        """🔴 Le babillard ne garde qu'une carte par source : sans réécriture,
        la carte annoncerait le code d'avant, qui ne mène plus nulle part.

        ⚠️ Mesuré dans le cœur : `session_code` est STOCKÉ et calculé une seule
        fois, depuis le jeton du sondage. Deux séances d'affilée gardent donc le
        même code — sauf si l'animateur le change, et le champ est fait pour ça
        (`readonly=False`, « personnalisez-le comme vous voulez »). C'est ce
        cas-là qui laisse une carte mentir.
        """
        sondage = self._seance()
        premier_code = sondage.session_code
        sondage.with_user(self.u_redactrice).action_annoncer_seance_au_babillard()
        sondage.sudo().action_end_session()
        sondage.sudo().session_code = "48351"
        sondage.sudo().action_start_session()
        self.assertNotEqual(premier_code, sondage.session_code)
        sondage.with_user(self.u_redactrice).action_annoncer_seance_au_babillard()
        carte = self._carte(sondage)
        self.assertEqual(len(carte), 1)
        self.assertIn("48351", carte.corps_html)
        self.assertNotIn(premier_code, carte.corps_html)

    def test_la_carte_remonte_en_tete_du_fil(self):
        """Le fil se range par date de publication : une séance qui s'ouvre
        aujourd'hui ne peut pas rester sous les cartes de la semaine."""
        sondage = self._seance()
        sondage.with_user(self.u_redactrice).action_annoncer_seance_au_babillard()
        carte = self._carte(sondage)
        vieille_date = fields.Datetime.now() - timedelta(days=9)
        carte.sudo().write({"date_publication": vieille_date})
        sondage.with_user(self.u_redactrice).action_annoncer_seance_au_babillard()
        carte.invalidate_recordset()
        self.assertGreater(carte.date_publication, vieille_date)

    def test_une_carte_retiree_du_fil_y_revient_avec_la_nouvelle_seance(self):
        sondage = self._seance()
        sondage.with_user(self.u_redactrice).action_annoncer_seance_au_babillard()
        carte = self._carte(sondage)
        carte.sudo().write({"state": "echue", "active": False})
        sondage.with_user(self.u_redactrice).action_annoncer_seance_au_babillard()
        carte.invalidate_recordset()
        self.assertTrue(carte.active)
        self.assertEqual(carte.state, "publie")

    def test_une_certification_refuse(self):
        """🔴 Le refus est celui du cœur : `_fetch_from_session_code` écarte
        toute certification, donc le code annoncé ne mènerait nulle part."""
        sondage = self._seance()
        sondage.sudo().certification = True
        with self.assertRaises(UserError):
            sondage.with_user(self.u_redactrice).action_annoncer_seance_au_babillard()

    def test_une_seance_sur_invitation_refuse(self):
        sondage = self._seance()
        sondage.sudo().access_mode = "token"
        with self.assertRaises(UserError):
            sondage.with_user(self.u_redactrice).action_annoncer_seance_au_babillard()

    def test_une_carte_d_une_autre_societe_refuse_au_lieu_de_la_deplacer(self):
        """🔴 Un sondage du cœur ne porte AUCUN `company_id` : tous les sondages
        se voient de toutes les sociétés, et la recherche de la carte se fait en
        `sudo()`, qui passe outre `bf_babillard.rule_post_societe` — le seul
        rempart. Sans le filtre de société, la carte posée par la société B
        était trouvée, écrasée, puis DÉPLACÉE en société A.

        ⚠️ Le refus est un `UserError` et non une création : la contrainte
        `source_unique` ne connaît pas la société, donc créer ferait remonter une
        IntegrityError brute, qui ne dit rien à la rédaction.
        """
        voisine = self.env["res.company"].sudo().create({"name": "Société voisine"})
        sondage = self._seance()
        carte = self.env["bf.babillard.post"].sudo()._depuis_source(
            "survey.survey", sondage.id, {
                "name": "Annonce de la voisine", "type_publication": "annonce",
                "audience": "tous", "corps_html": "<p>Chez la voisine.</p>",
                "company_id": voisine.id})
        with self.assertRaises(UserError):
            sondage.with_user(self.u_redactrice).action_annoncer_seance_au_babillard()
        carte.invalidate_recordset()
        self.assertEqual(carte.company_id, voisine)
        self.assertEqual(carte.name, "Annonce de la voisine")
        self.assertNotIn(sondage.session_code, carte.corps_html)

    def test_la_republication_vide_l_echeance_que_le_cron_ferait_echoir(self):
        """🔴 Le cron quotidien `_cron_echoir` repasse à « échue » toute carte
        publiée dont l'échéance est dépassée. Une carte du mois dernier, échue
        le 3 et republiée ce matin pour la séance de 10 h, gardait son échéance
        et retombait du fil au prochain passage du cron — pendant la séance.
        """
        sondage = self._seance()
        sondage.with_user(self.u_redactrice).action_annoncer_seance_au_babillard()
        carte = self._carte(sondage)
        carte.sudo().write({
            "date_echeance": fields.Date.context_today(carte) - timedelta(days=6)})
        sondage.with_user(self.u_redactrice).action_annoncer_seance_au_babillard()
        carte.invalidate_recordset()
        self.assertFalse(carte.date_echeance)
        self.env["bf.babillard.post"].sudo()._cron_echoir()
        carte.invalidate_recordset()
        self.assertEqual(carte.state, "publie")

    def test_sans_la_redaction_personne_n_annonce_une_seance(self):
        """🔴 La méthode est publique : le `groups=` du bouton ne garde que
        l'écran."""
        sondage = self._seance()
        with self.assertRaises(AccessError):
            sondage.with_user(self.u_simple).action_annoncer_seance_au_babillard()
        self.assertFalse(self._carte(sondage))
