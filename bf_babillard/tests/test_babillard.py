# -*- coding: utf-8 -*-
"""Essais du babillard.

⚠️ Aucun essai ne tourne sous `uid 1` : le superutilisateur n'a pas de groupes,
donc il ne prouve rien sur les règles d'enregistrement. Chaque parcours est joué
sous un compte réel, dans le rôle visé.
"""
import io
import os
import re

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestBabillard(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.societe = cls.env.company
        Users = cls.env["res.users"].with_context(no_reset_password=True)
        cls.g_redaction = cls.env.ref("bf_babillard.group_babillard_redacteur")
        cls.g_moderation = cls.env.ref("bf_babillard.group_babillard_moderation")
        cls.g_interne = cls.env.ref("base.group_user")

        cls.dept_atelier = cls.env["hr.department"].create({"name": "Atelier"})
        cls.dept_bureau = cls.env["hr.department"].create({"name": "Bureau"})

        cls.u_redactrice = Users.create({
            "name": "Rédactrice", "login": "babillard_redactrice",
            "email": "redaction@exemple.test",
            "groups_id": [(6, 0, [cls.g_interne.id, cls.g_redaction.id])]})
        cls.u_atelier = Users.create({
            "name": "Personne de l'atelier", "login": "babillard_atelier",
            # ⚠️ Sans courriel, `message_post` lève « configure the sender's
            # email address » et l'essai accuse le module à tort.
            "email": "atelier@exemple.test",
            "groups_id": [(6, 0, [cls.g_interne.id])]})
        cls.u_bureau = Users.create({
            "name": "Personne du bureau", "login": "babillard_bureau",
            "groups_id": [(6, 0, [cls.g_interne.id])]})
        cls.u_moderation = Users.create({
            "name": "Personne désignée", "login": "babillard_moderation",
            "groups_id": [(6, 0, [cls.g_interne.id, cls.g_moderation.id])]})

        Employe = cls.env["hr.employee"]
        cls.e_atelier = Employe.create({
            "name": "Personne de l'atelier", "user_id": cls.u_atelier.id,
            "department_id": cls.dept_atelier.id})
        cls.e_bureau = Employe.create({
            "name": "Personne du bureau", "user_id": cls.u_bureau.id,
            "department_id": cls.dept_bureau.id})

    def _publication(self, **kw):
        vals = {"name": "Fermeture du 24 décembre", "audience": "tous"}
        vals.update(kw)
        post = self.env["bf.babillard.post"].with_user(self.u_redactrice).create(vals)
        return post

    def _signaler(self, user, post, motif="Propos blessants dans un commentaire.", **kw):
        """Signaler comme à l'écran : par l'assistant, au nom de la personne."""
        self.env["bf.babillard.signalement.assistant"].with_user(user).create(
            dict({"post_ref": post.id, "motif": motif}, **kw)).action_envoyer()
        return self.env["bf.babillard.signalement"].sudo().search(
            [("post_id", "=", post.id)], order="id desc", limit=1)

    def _gestionnaire_de(self, *employes, departement=None):
        """Un compte qui a des subordonnés DIRECTS, comme à l'organigramme.

        Créé dans l'essai, pas au montage : un compte interne de plus change le
        nombre de destinataires d'une audience « tout le personnel », donc les
        décomptes d'envoi des autres essais.
        """
        user = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Gestionnaire", "login": "babillard_gestionnaire",
            "email": "gestion@exemple.test",
            "groups_id": [(6, 0, [self.g_interne.id])]})
        employe = self.env["hr.employee"].create({
            "name": "Gestionnaire", "user_id": user.id,
            "department_id": (departement or self.dept_atelier).id})
        for subordonne in employes:
            subordonne.sudo().parent_id = employe.id
        return user, employe

    # --- audience ---------------------------------------------------------

    def test_audience_tous_inclut_les_internes(self):
        post = self._publication()
        destinataires = post.sudo()._destinataires()
        self.assertIn(self.u_atelier, destinataires)
        self.assertIn(self.u_bureau, destinataires)

    def test_audience_par_departement_exclut_les_autres(self):
        post = self._publication(audience="departements",
                                 department_ids=[(6, 0, [self.dept_atelier.id])])
        destinataires = post.sudo()._destinataires()
        self.assertIn(self.u_atelier, destinataires)
        self.assertNotIn(self.u_bureau, destinataires)

    def test_audience_par_groupe(self):
        post = self._publication(audience="groupes",
                                 group_ids=[(6, 0, [self.g_redaction.id])])
        destinataires = post.sudo()._destinataires()
        self.assertIn(self.u_redactrice, destinataires)
        self.assertNotIn(self.u_atelier, destinataires)

    def test_audience_vide_est_refusee(self):
        with self.assertRaises(UserError):
            self._publication(audience="departements")

    # --- la garde est dans la règle, pas dans l'écran ----------------------

    def test_hors_audience_ne_lit_pas_la_publication(self):
        post = self._publication(audience="departements",
                                 department_ids=[(6, 0, [self.dept_atelier.id])])
        post.action_publier()
        lisibles = self.env["bf.babillard.post"].with_user(self.u_bureau).search([])
        self.assertNotIn(post, lisibles)
        self.assertIn(post, self.env["bf.babillard.post"].with_user(self.u_atelier).search([]))

    def test_brouillon_invisible_au_personnel(self):
        post = self._publication()
        self.assertNotIn(post, self.env["bf.babillard.post"].with_user(self.u_atelier).search([]))
        post.action_publier()
        self.assertIn(post, self.env["bf.babillard.post"].with_user(self.u_atelier).search([]))

    def test_personnel_ne_peut_pas_ecrire(self):
        post = self._publication()
        post.action_publier()
        with self.assertRaises(AccessError):
            post.with_user(self.u_atelier).write({"name": "Titre détourné"})

    # --- accusé de lecture -------------------------------------------------

    def test_accuse_est_idempotent(self):
        post = self._publication(lecture_requise=True)
        post.action_publier()
        vue = post.with_user(self.u_atelier)
        vue.action_marquer_lu()
        vue.action_marquer_lu()
        self.assertEqual(post.sudo().nb_lectures, 1)
        self.assertTrue(vue.lu_par_moi)

    def test_accuse_refuse_hors_audience(self):
        """La garde du code mord là où la règle ne mord pas.

        🔴 Jouée avec une personne du bureau, cette preuve ne valait rien : la
        règle d'enregistrement lui refusait déjà la LECTURE, donc la garde
        n'était jamais atteinte et un `if False:` passait inaperçu. La rédactrice,
        elle, lit tout le babillard : c'est le seul rôle où la garde décide.
        """
        post = self._publication(lecture_requise=True, audience="departements",
                                 department_ids=[(6, 0, [self.dept_atelier.id])])
        post.action_publier()
        vue = post.with_user(self.u_redactrice)
        self.assertTrue(vue.name, "la rédactrice lit bien la publication")
        with self.assertRaises(AccessError):
            vue.action_marquer_lu()
        self.assertEqual(post.sudo().nb_lectures, 0)

    def test_accuse_refuse_a_qui_ne_lit_pas(self):
        post = self._publication(lecture_requise=True, audience="departements",
                                 department_ids=[(6, 0, [self.dept_atelier.id])])
        post.action_publier()
        with self.assertRaises(AccessError):
            post.with_user(self.u_bureau).action_marquer_lu()

    def test_accuse_refuse_avant_publication(self):
        post = self._publication(lecture_requise=True)
        with self.assertRaises(UserError):
            post.sudo().with_user(self.u_atelier).action_marquer_lu()

    def test_accuse_ne_se_pose_pas_en_direct(self):
        """Écrire dans la table sautait l'état, l'audience et la lecture obligatoire.

        Un brouillon sans lecture obligatoire : les trois gardes du bouton y
        diraient non, et la table seule n'en dit rien.
        """
        post = self._publication()
        with self.assertRaises(AccessError):
            self.env["bf.babillard.lecture"].with_user(self.u_atelier).create({
                "post_id": post.id, "user_id": self.u_bureau.id})
        self.assertEqual(post.sudo().nb_lectures, 0)

    def test_personne_ne_lit_l_accuse_d_un_autre(self):
        post = self._publication(lecture_requise=True)
        post.action_publier()
        post.with_user(self.u_atelier).action_marquer_lu()
        vues = self.env["bf.babillard.lecture"].with_user(self.u_bureau).search([])
        self.assertFalse(vues)
        self.assertTrue(self.env["bf.babillard.lecture"].with_user(self.u_redactrice).search([]))

    def test_manquants_sont_les_destinataires_moins_les_lecteurs(self):
        post = self._publication(lecture_requise=True, audience="departements",
                                 department_ids=[(6, 0, [self.dept_atelier.id,
                                                         self.dept_bureau.id])])
        post.action_publier()
        post.with_user(self.u_atelier).action_marquer_lu()
        action = post.with_user(self.u_redactrice).action_voir_manquants()
        manquants = action["domain"][0][2]
        self.assertIn(self.u_bureau.id, manquants)
        self.assertNotIn(self.u_atelier.id, manquants)

    def test_manquants_reserves_a_la_redaction(self):
        """Le bouton est caché au personnel ; la méthode, elle, s'appelle par RPC."""
        post = self._publication(lecture_requise=True)
        post.action_publier()
        with self.assertRaises(AccessError):
            post.with_user(self.u_atelier).action_voir_manquants()

    # --- commentaires, échéance, filtres ----------------------------------

    def test_lecture_obligatoire_ferme_les_commentaires(self):
        post = self._publication(lecture_requise=True)
        self.assertFalse(post.commentaires_ouverts)
        post.write({"commentaires_ouverts": True})
        self.assertTrue(post.commentaires_ouverts,
                        "la fermeture est un défaut, pas un verrou")

    def test_cron_fait_tomber_les_echues(self):
        hier = fields.Date.subtract(fields.Date.context_today(self.env.user), days=1)
        post = self._publication(date_echeance=hier)
        post.action_publier()
        self.env["bf.babillard.post"]._cron_echoir()
        self.assertEqual(post.state, "echue")
        self.assertNotIn(post, self.env["bf.babillard.post"].with_user(self.u_atelier).search([]))

    def test_echeance_du_jour_reste_au_fil(self):
        post = self._publication(date_echeance=fields.Date.context_today(self.env.user))
        post.action_publier()
        self.env["bf.babillard.post"]._cron_echoir()
        self.assertEqual(post.state, "publie")

    def test_filtre_a_lire(self):
        lue = self._publication(name="Déjà lue", lecture_requise=True)
        a_lire = self._publication(name="Pas encore lue", lecture_requise=True)
        (lue | a_lire).action_publier()
        lue.with_user(self.u_atelier).action_marquer_lu()
        trouvees = self.env["bf.babillard.post"].with_user(self.u_atelier).search([
            ("lecture_requise", "=", True), ("lu_par_moi", "=", False)])
        self.assertIn(a_lire, trouvees)
        self.assertNotIn(lue, trouvees)

    def test_retrait_conserve_les_accuses(self):
        post = self._publication(lecture_requise=True)
        post.action_publier()
        post.with_user(self.u_atelier).action_marquer_lu()
        post.action_retirer()
        self.assertEqual(post.sudo().nb_lectures, 1)

    def test_commentaires_fermes_refusent_un_message(self):
        """Le drapeau garde, il ne décore pas."""
        post = self._publication(lecture_requise=True)
        post.action_publier()
        self.assertFalse(post.commentaires_ouverts)
        with self.assertRaises(UserError):
            post.with_user(self.u_atelier).message_post(
                body="Je ne suis pas d'accord", message_type="comment",
                subtype_xmlid="mail.mt_comment")

    def test_commentaires_fermes_laissent_passer_le_suivi(self):
        """Le suivi écrit l'histoire de la publication : il doit passer.

        ⚠️ Éprouvé par un message de type `notification` posé à la main, et non
        par un `write` sur un champ suivi : le suivi d'Odoo s'écrit au
        PRÉ-COMMIT, donc rien n'apparaît dans la transaction de l'essai.
        """
        post = self._publication(lecture_requise=True)
        post.action_publier()
        avant = len(post.sudo().message_ids)
        post.sudo().message_post(body="Retirée par la modération",
                                 message_type="notification")
        self.assertGreater(len(post.sudo().message_ids), avant)

    def test_commentaires_fermes_refusent_tout_type_de_message(self):
        """Le navigateur choisit le type qu'il envoie : la garde ne s'y fie pas."""
        post = self._publication(lecture_requise=True)
        post.action_publier()
        for type_message in ("notification", "comment"):
            with self.assertRaises(UserError):
                post.with_user(self.u_atelier).message_post(
                    body="Contournement", message_type=type_message)

    def test_commentaires_fermes_refusent_la_reponse_par_courriel(self):
        """La passerelle poste en superutilisateur, et en type `email`."""
        post = self._publication(lecture_requise=True)
        post.action_publier()
        with self.assertRaises(UserError):
            post.sudo().message_post(body="Réponse au courriel", message_type="email")

    def test_commentaires_fermes_laissent_la_note_de_l_equipe(self):
        post = self._publication(lecture_requise=True)
        post.action_publier()
        note = post.with_user(self.u_redactrice).message_post(
            body="Relancer l'équipe de nuit", message_type="comment",
            subtype_xmlid="mail.mt_note")
        self.assertTrue(note)

    def test_une_mention_ne_sort_pas_de_l_audience(self):
        post = self._publication(audience="departements",
                                 department_ids=[(6, 0, [self.dept_atelier.id])])
        post.action_publier()
        externe = self.env["res.partner"].create(
            {"name": "Contact externe", "email": "externe@exemple.test"})
        message = post.with_user(self.u_atelier).message_post(
            body="Regarde ça", message_type="comment", subtype_xmlid="mail.mt_comment",
            partner_ids=[externe.id, self.u_bureau.partner_id.id])
        self.assertNotIn(externe, message.sudo().partner_ids)
        self.assertNotIn(self.u_bureau.partner_id, message.sudo().partner_ids)

    def test_commentaires_ouverts_acceptent_un_message(self):
        post = self._publication(lecture_requise=False)
        post.action_publier()
        message = post.with_user(self.u_atelier).message_post(
            body="Merci pour l'info", message_type="comment",
            subtype_xmlid="mail.mt_comment")
        self.assertTrue(message)

    def test_journee_de_publication(self):
        post = self._publication()
        self.assertFalse(post.date_jour)
        post.action_publier()
        self.assertEqual(
            post.date_jour,
            fields.Datetime.context_timestamp(post, post.date_publication).date())

    # --- signalement -------------------------------------------------------

    def test_signalement_est_confidentiel(self):
        """La modération le lit ; ni les collègues, ni la personne qui l'a fait.

        🔴 La personne qui signale lisait son dossier, donc aussi la suite
        donnée et les échanges de la modération.
        """
        post = self._publication()
        post.action_publier()
        sig = self._signaler(self.u_atelier, post)
        self.assertTrue(sig)
        Sig = self.env["bf.babillard.signalement"]
        self.assertIn(sig, Sig.with_user(self.u_moderation).search([]))
        # Hors modération, le modèle entier est fermé : la recherche lève.
        for personne in (self.u_bureau, self.u_atelier):
            with self.assertRaises(AccessError):
                Sig.with_user(personne).search([])
        self.assertNotIn(self.u_atelier.partner_id, sig.message_partner_ids,
                         "qui signale ne devient pas abonné du dossier")

    def test_moderation_retire_la_publication(self):
        post = self._publication()
        post.action_publier()
        sig = self._signaler(self.u_atelier, post, motif="À retirer.")
        sig.with_user(self.u_moderation).action_retirer_la_publication()
        self.assertEqual(post.sudo().state, "echue")

    def test_redaction_ne_lit_pas_les_signalements(self):
        """La rédaction publie ; elle ne reçoit pas les plaintes."""
        post = self._publication()
        post.action_publier()
        self._signaler(self.u_atelier, post, motif="Confidentiel.")
        with self.assertRaises(AccessError):
            self.env["bf.babillard.signalement"].with_user(self.u_redactrice).search([])

    def test_le_dialogue_dit_qui_signale(self):
        """Le dialogue s'ouvre sur un enregistrement neuf : le nom doit y être d'office."""
        post = self._publication(name="Consigne du quai")
        post.action_publier()
        valeurs = self.env["bf.babillard.signalement.assistant"].with_user(
            self.u_atelier).with_context(default_post_ref=post.id).default_get(
                ["auteur_nom", "post_ref", "post_titre"])
        self.assertEqual(valeurs.get("auteur_nom"), self.u_atelier.name)
        self.assertEqual(valeurs.get("post_titre"), "Consigne du quai",
                         "le titre s'affiche sur l'enregistrement neuf du dialogue")

    def test_on_ne_signale_que_ce_qu_on_lit(self):
        """Un identifiant choisi au hasard ne rend pas le titre d'un brouillon."""
        brouillon = self._publication(name="Brouillon confidentiel")
        with self.assertRaises(AccessError):
            self.env["bf.babillard.signalement.assistant"].with_user(self.u_atelier).create(
                {"post_ref": brouillon.id, "motif": "Curiosité."})

    def test_le_commentaire_vise_appartient_a_la_publication(self):
        post = self._publication()
        post.action_publier()
        ailleurs = self.env["res.partner"].create({"name": "Fiche sans rapport"})
        message = ailleurs.message_post(body="Message d'une autre fiche")
        with self.assertRaises(AccessError):
            self.env["bf.babillard.signalement.assistant"].with_user(self.u_atelier).create(
                {"post_ref": post.id, "message_ref": message.id, "motif": "Détour."})

    def test_le_dialogue_est_prive(self):
        """🔴 Un enregistrement transitoire n'est pas réservé à son créateur."""
        post = self._publication()
        post.action_publier()
        Assistant = self.env["bf.babillard.signalement.assistant"]
        Assistant.with_user(self.u_atelier).create(
            {"post_ref": post.id, "motif": "Motif que personne d'autre ne lit."})
        self.assertFalse(Assistant.with_user(self.u_bureau).search([]))
        self.assertTrue(Assistant.with_user(self.u_atelier).search([]))

    def test_le_dialogue_ne_rend_pas_le_titre_d_un_brouillon(self):
        """C'est le chemin de `onchange` : les défauts viennent du contexte."""
        brouillon = self._publication(name="Brouillon confidentiel")
        Assistant = self.env["bf.babillard.signalement.assistant"].with_user(self.u_atelier)
        with self.assertRaises(AccessError):
            Assistant.with_context(default_post_ref=brouillon.id).default_get(
                ["post_ref", "post_titre"])

    def test_la_personne_visee_est_figee(self):
        """🔴 L'auteur d'un commentaire peut réécrire `author_id` : le dossier ne suit pas."""
        post = self._publication(lecture_requise=False)
        post.action_publier()
        commentaire = post.with_user(self.u_atelier).message_post(
            body="Propos déplacés", message_type="comment",
            subtype_xmlid="mail.mt_comment")
        sig = self._signaler(self.u_bureau, post, message_ref=commentaire.id)
        self.assertEqual(sig.cible_partner_id, self.u_atelier.partner_id)
        commentaire.sudo().write({"author_id": self.u_moderation.partner_id.id})
        self.assertEqual(sig.cible_partner_id, self.u_atelier.partner_id,
                         "la personne visée est un constat, pas un calcul")

    def test_le_contenu_signale_survit_a_l_effacement(self):
        post = self._publication(lecture_requise=False)
        post.action_publier()
        commentaire = post.with_user(self.u_atelier).message_post(
            body="<p>La preuve</p>", message_type="comment",
            subtype_xmlid="mail.mt_comment")
        sig = self._signaler(self.u_bureau, post, message_ref=commentaire.id)
        commentaire.sudo().unlink()
        self.assertIn("La preuve", sig.extrait or "")

    def test_le_dossier_ne_se_reecrit_pas(self):
        post = self._publication()
        post.action_publier()
        sig = self._signaler(self.u_atelier, post)
        vue = sig.with_user(self.u_moderation)
        vue.write({"state": "en_cours", "suite": "Rencontre tenue"})
        for interdit in ({"motif": "Réécrit"}, {"auteur_signalement_id": self.u_bureau.id},
                         {"escalade": False}, {"post_id": post.id}):
            with self.assertRaises(AccessError):
                vue.write(interdit)

    def test_un_message_cree_en_direct_ne_contourne_pas_la_fermeture(self):
        """🔴 `_mail_post_access = "read"` laisse créer un message sans `message_post`."""
        post = self._publication(lecture_requise=True)
        post.action_publier()
        with self.assertRaises(UserError):
            self.env["mail.message"].with_user(self.u_atelier).create({
                "model": post._name, "res_id": post.id, "body": "Contournement",
                "message_type": "comment",
                "subtype_id": self.env.ref("mail.mt_comment").id})

    def test_une_mention_en_direct_ne_sort_pas_de_l_audience(self):
        post = self._publication(audience="departements", lecture_requise=False,
                                 department_ids=[(6, 0, [self.dept_atelier.id])])
        post.action_publier()
        externe = self.env["res.partner"].create({"name": "Contact externe direct"})
        message = self.env["mail.message"].with_user(self.u_atelier).create({
            "model": post._name, "res_id": post.id, "body": "Regarde",
            "message_type": "comment", "subtype_id": self.env.ref("mail.mt_comment").id,
            "partner_ids": [(6, 0, [externe.id, self.u_bureau.partner_id.id])]})
        self.assertFalse(message.sudo().partner_ids)

    def test_prevenir_a_la_main_est_refuse(self):
        """🔴 `message_notify` est publique : appelable par RPC par n'importe qui."""
        post = self._publication()
        post.action_publier()
        with self.assertRaises(AccessError):
            post.with_user(self.u_atelier).message_notify(
                partner_ids=[self.u_bureau.partner_id.id], body="Coucou")

    def test_accuse_refuse_sans_lecture_obligatoire(self):
        post = self._publication(lecture_requise=False)
        post.action_publier()
        with self.assertRaises(UserError):
            post.with_user(self.u_atelier).action_marquer_lu()

    def test_un_signalement_nait_nouveau(self):
        """Ni état, ni suite, ni responsable ne se choisissent à la création."""
        post = self._publication()
        post.action_publier()
        sig = self.env["bf.babillard.signalement"].with_user(self.u_moderation).create({
            "post_id": post.id, "motif": "Essai.", "state": "traite",
            "suite": "Suite inventée"})
        self.assertEqual(sig.sudo().state, "nouveau")
        self.assertFalse(sig.sudo().suite)

    def test_une_autre_societe_ne_lit_pas_le_babillard(self):
        voisine = self.env["res.company"].create({"name": "Société voisine"})
        voisin = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Personne de la société voisine", "login": "babillard_voisin",
            "company_id": voisine.id, "company_ids": [(6, 0, [voisine.id])],
            "groups_id": [(6, 0, [self.g_interne.id, self.g_moderation.id])]})
        post = self._publication()
        post.action_publier()
        self.assertNotIn(post, self.env["bf.babillard.post"].with_user(voisin).search([]))
        sig = self._signaler(self.u_atelier, post)
        self.assertNotIn(sig, self.env["bf.babillard.signalement"].with_user(voisin).search([]))
        self.assertNotIn(voisin, sig._moderation())

    # --- l'entrée des ponts -----------------------------------------------

    def test_carte_de_source_est_idempotente(self):
        Post = self.env["bf.babillard.post"]
        une = Post._depuis_source("bf.test.source", 7, {
            "name": "Carte d'un pont", "type_publication": "celebration"})
        encore = Post._depuis_source("bf.test.source", 7, {
            "name": "Carte d'un pont", "type_publication": "celebration"})
        self.assertEqual(une, encore)
        self.assertEqual(Post.sudo().search_count(
            [("source_model", "=", "bf.test.source")]), 1)

    def test_carte_de_source_nait_publiee(self):
        post = self.env["bf.babillard.post"]._depuis_source("bf.test.source", 8, {
            "name": "Carte publiée d'office"})
        self.assertEqual(post.state, "publie")
        self.assertTrue(post.date_publication)
        self.assertIn(post, self.env["bf.babillard.post"].with_user(self.u_atelier).search([]))

    # --- la promesse : rien qui mesure une personne ------------------------

    def test_aucun_champ_de_mesure_sur_l_accuse(self):
        """L'accusé prouve la diffusion. Il ne compte pas les visites."""
        champs = set(self.env["bf.babillard.lecture"]._fields)
        interdits = {"score", "nb_vues", "duree", "engagement", "points"}
        self.assertFalse(champs & interdits)
        self.assertEqual(champs - {"id", "display_name", "create_uid", "create_date",
                                   "write_uid", "write_date", "post_id", "user_id", "date",
                                   "company_id"},
                         set(), "un champ neuf sur l'accusé se décide, il ne s'ajoute pas")

    # --- ce que le lecteur voit -------------------------------------------

    def test_a_lire_pour_moi_vrai_pour_qui_est_vise(self):
        post = self._publication(lecture_requise=True)
        post.with_user(self.u_redactrice).action_publier()
        self.assertTrue(post.with_user(self.u_atelier).a_lire_pour_moi)

    def test_a_lire_pour_moi_faux_hors_audience(self):
        """🔴 Le bouton « J'ai lu » paraissait à la rédaction sur une annonce
        adressée à un département dont elle ne fait pas partie."""
        post = self._publication(
            lecture_requise=True, audience="departements",
            department_ids=[(6, 0, [self.dept_atelier.id])])
        post.with_user(self.u_redactrice).action_publier()
        self.assertTrue(post.with_user(self.u_atelier).a_lire_pour_moi)
        self.assertFalse(post.with_user(self.u_redactrice).a_lire_pour_moi)

    def test_a_lire_pour_moi_faux_une_fois_confirmee(self):
        post = self._publication(lecture_requise=True)
        post.with_user(self.u_redactrice).action_publier()
        post.with_user(self.u_atelier).action_marquer_lu()
        self.assertFalse(post.with_user(self.u_atelier).a_lire_pour_moi)

    def test_lu_par_moi_ne_repond_pas_pour_quelqu_un_d_autre(self):
        """🔴 Le champ était mis en cache par publication, pas par personne.

        Une mutation a survécu à toute la passe : aucun essai ne lisait
        `lu_par_moi` pour deux personnes dans la même transaction, ce qui est
        pourtant ce qui arrive dans un cron ou un `with_user`.
        """
        post = self._publication(lecture_requise=True)
        post.with_user(self.u_redactrice).action_publier()
        post.with_user(self.u_atelier).action_marquer_lu()
        self.assertTrue(post.with_user(self.u_atelier).lu_par_moi)
        self.assertFalse(post.with_user(self.u_bureau).lu_par_moi)

    def test_a_lire_pour_moi_faux_sur_un_brouillon(self):
        post = self._publication(lecture_requise=True)
        self.assertFalse(post.with_user(self.u_redactrice).a_lire_pour_moi)

    def test_a_lire_pour_moi_suit_l_autorite_de_l_audience(self):
        """La mise en cache par audience ne doit pas répondre pour une autre.

        ⚠️ Le calcul retient `_destinataires()` par audience identique. Deux
        publications d'audiences DIFFÉRENTES lues dans le même lot doivent donc
        donner deux réponses différentes, sinon la clé est trop large.
        """
        visee = self._publication(
            name="Pour l'atelier", lecture_requise=True, audience="departements",
            department_ids=[(6, 0, [self.dept_atelier.id])])
        autre = self._publication(
            name="Pour le bureau", lecture_requise=True, audience="departements",
            department_ids=[(6, 0, [self.dept_bureau.id])])
        (visee | autre).with_user(self.u_redactrice).action_publier()
        lot = (visee | autre).with_user(self.u_atelier)
        self.assertEqual(
            {p.name: p.a_lire_pour_moi for p in lot},
            {"Pour l'atelier": True, "Pour le bureau": False})

    def test_le_compte_du_fil_ne_dit_que_des_cardinaux(self):
        """⚠️ Le compte est LU d'abord, puis relu après le commentaire.

        Sans cette première lecture, l'essai ne prouve que le calcul, jamais
        son invalidation : une mutation qui retirait `@api.depends`
        ("message_ids") lui survivait sans rien casser.
        """
        post = self._publication()
        post.with_user(self.u_redactrice).action_publier()
        vue = post.with_user(self.u_bureau)
        self.assertEqual(vue.nb_commentaires, 0)
        self.assertEqual(vue.nb_reactions, 0)
        message = post.with_user(self.u_atelier).message_post(
            body="Bien reçu.", message_type="comment",
            subtype_xmlid="mail.mt_comment")
        self.env["mail.message.reaction"].sudo().create({
            "message_id": message.id, "content": "👍",
            "partner_id": self.u_bureau.partner_id.id})
        self.assertEqual(vue.nb_commentaires, 1)
        self.assertEqual(vue.nb_reactions, 1)
        # ⚠️ Le chemin qui ne passe PAS par `message_post` : un message créé en
        # direct n'invalide rien de lui-même, c'est la dépendance qui doit le
        # faire. C'est aussi le chemin qu'emprunte la passerelle de courriel.
        self.env["mail.message"].with_user(self.u_atelier).create({
            "model": "bf.babillard.post", "res_id": post.id,
            "body": "Une deuxième fois.", "message_type": "comment",
            "subtype_id": self.env.ref("mail.mt_comment").id,
        })
        self.assertEqual(vue.nb_commentaires, 2)

    def test_le_compte_du_fil_ignore_les_notifications(self):
        """Le suivi et les avis ne sont pas des commentaires."""
        post = self._publication(lecture_requise=True)
        post.with_user(self.u_redactrice).action_publier()
        post.invalidate_recordset()
        self.assertEqual(post.with_user(self.u_atelier).nb_commentaires, 0)

    def test_une_note_interne_ne_compte_pas_comme_un_commentaire(self):
        """🔴 Une note interne est un message de type « comment ».

        Le module laisse la modération noter en privé sous une publication dont
        les commentaires sont FERMÉS. La carte annonçait « 1 commentaire » à
        toute l'audience pour une note qu'elle ne peut pas lire.
        """
        post = self._publication(lecture_requise=True)
        post.with_user(self.u_redactrice).action_publier()
        self.assertFalse(post.commentaires_ouverts)
        post.with_user(self.u_redactrice).message_post(
            body="Note pour la rédaction.", message_type="comment",
            subtype_xmlid="mail.mt_note")
        self.assertEqual(post.with_user(self.u_atelier).nb_commentaires, 0)

    def test_un_employe_supprime_ne_casse_pas_le_fil(self):
        """🔴 `personne_id` vise une VUE SQL : aucune clé étrangère, donc aucun
        `ondelete`. Un identifiant mort faisait lever MissingError au champ lié,
        et le fil entier tombait pour toute l'audience."""
        employe = self.env["hr.employee"].create({"name": "Personne de passage"})
        post = self._publication(personne_id=employe.id)
        post.with_user(self.u_redactrice).action_publier()
        self.assertEqual(post.personne_id.id, employe.id)
        employe.unlink()
        vue = post.with_user(self.u_atelier)
        vue.invalidate_recordset()
        self.assertFalse(vue.personne_id)
        self.assertFalse(vue.personne_nom)

    def test_le_relais_ne_parait_pas_si_l_equipe_n_est_pas_visee(self):
        """🔴 Le bouton se contentait d'avoir des subordonnés : il paraissait
        sur une annonce adressée à un autre département, et le clic ouvrait une
        liste vide.

        Le montage reproduit exactement ce cas : le gestionnaire est au Bureau,
        donc il LIT l'annonce du Bureau ; son équipe est à l'Atelier, donc elle
        n'est pas visée.
        """
        gestionnaire, _employe = self._gestionnaire_de(
            self.e_atelier, departement=self.dept_bureau)
        pour_le_bureau = self._publication(
            lecture_requise=True, audience="departements",
            department_ids=[(6, 0, [self.dept_bureau.id])])
        pour_le_bureau.with_user(self.u_redactrice).action_publier()
        self.assertFalse(pour_le_bureau.with_user(gestionnaire).peut_relancer_equipe)
        pour_l_atelier = self._publication(
            lecture_requise=True, audience="departements",
            department_ids=[(6, 0, [self.dept_atelier.id, self.dept_bureau.id])])
        pour_l_atelier.with_user(self.u_redactrice).action_publier()
        self.assertTrue(pour_l_atelier.with_user(gestionnaire).peut_relancer_equipe)

    def test_la_personne_mise_en_avant_est_lisible_par_l_audience(self):
        """⚠️ `hr.employee` ne se lit pas comme les autres modèles.

        Odoo 18 sert ses champs PUBLICS à tout le monde, par un détour vers
        `hr.employee.public`, et refuse les privés. Le lien du babillard pointe
        donc le modèle public : explicite, et sans dépendre de ce détour. L'essai
        tient les deux bouts, le refus d'un champ privé et la lecture du visage.
        """
        # ⚠️ Le refus vit dans `fetch`, donc il ne se déclenche que sur un champ
        # STOCKÉ : un champ calculé non stocké ne passe jamais par là et se lit
        # sans bruit. Un essai monté sur le premier champ privé venu passait
        # donc pour la mauvaise raison.
        champs = self.env["hr.employee"]._fields
        prives = sorted(nom for nom in
                        set(champs) - set(self.env["hr.employee.public"]._fields)
                        if champs[nom].store)
        self.assertTrue(prives, "hr.employee n'a plus de champ privé stocké")
        with self.assertRaises(AccessError):
            self.env["hr.employee"].with_user(self.u_bureau).browse(
                self.e_atelier.id).read([prives[0]])
        post = self._publication(personne_id=self.e_atelier.id)
        post.with_user(self.u_redactrice).action_publier()
        vue = post.with_user(self.u_bureau)
        self.assertEqual(vue.personne_id.name, "Personne de l'atelier")
        self.assertTrue(vue.personne_avatar)
        # Le nom lu par l'en-tête passe par un champ lié : c'est LUI que
        # l'écran affiche, et il traverse le même contrôle d'accès.
        self.assertEqual(vue.personne_nom, "Personne de l'atelier")

    def test_les_vues_se_chargent_dans_chaque_role(self):
        """Une vue qui nomme un champ absent tombe DANS LE NAVIGATEUR.

        `get_views` ne rejoue pas le client, mais il valide l'arbre et les
        droits de chaque champ : c'est le filet le moins cher contre un champ
        oublié dans le gabarit d'une carte.
        """
        vues = [(False, "kanban"), (False, "form"), (False, "list"), (False, "search")]
        for user in (self.u_redactrice, self.u_atelier, self.u_moderation):
            rendu = self.env["bf.babillard.post"].with_user(user).get_views(vues)
            self.assertEqual(set(rendu["views"]), {"kanban", "form", "list", "search"})

    # --- le relais du gestionnaire ----------------------------------------

    def test_le_gestionnaire_voit_qui_de_son_equipe_n_a_pas_lu(self):
        gestionnaire, _employe = self._gestionnaire_de(self.e_atelier)
        post = self._publication(lecture_requise=True)
        post.with_user(self.u_redactrice).action_publier()
        action = post.with_user(gestionnaire).action_voir_manquants_equipe()
        manquants = action["domain"][0][2]
        self.assertIn(self.u_atelier.id, manquants)
        self.assertNotIn(self.u_bureau.id, manquants,
                         "le bureau ne relève pas de ce gestionnaire")

    def test_le_gestionnaire_ne_voit_plus_qui_a_confirme(self):
        gestionnaire, _employe = self._gestionnaire_de(self.e_atelier)
        post = self._publication(lecture_requise=True)
        post.with_user(self.u_redactrice).action_publier()
        post.with_user(self.u_atelier).action_marquer_lu()
        action = post.with_user(gestionnaire).action_voir_manquants_equipe()
        self.assertNotIn(self.u_atelier.id, action["domain"][0][2])

    def test_le_relais_s_arrete_aux_subordonnes_directs(self):
        """Un directeur ne récupère pas l'arborescence entière."""
        gestionnaire, employe_gestionnaire = self._gestionnaire_de(self.e_atelier)
        directrice = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Directrice", "login": "babillard_directrice",
            "email": "direction@exemple.test",
            "groups_id": [(6, 0, [self.g_interne.id])]})
        employe_directrice = self.env["hr.employee"].create({
            "name": "Directrice", "user_id": directrice.id})
        employe_gestionnaire.sudo().parent_id = employe_directrice.id
        post = self._publication(lecture_requise=True)
        post.with_user(self.u_redactrice).action_publier()
        action = post.with_user(directrice).action_voir_manquants_equipe()
        manquants = action["domain"][0][2]
        self.assertIn(gestionnaire.id, manquants)
        self.assertNotIn(self.u_atelier.id, manquants,
                         "l'atelier relève du gestionnaire, pas de la directrice")

    def test_sans_equipe_le_relais_est_refuse(self):
        post = self._publication(lecture_requise=True)
        post.with_user(self.u_redactrice).action_publier()
        with self.assertRaises(AccessError):
            post.with_user(self.u_atelier).action_voir_manquants_equipe()

    def test_le_relais_refuse_une_publication_qu_on_ne_peut_pas_lire(self):
        """🔴 La méthode est publique : sans contrôle d'accès AVANT tout, un
        gestionnaire interrogeait n'importe quelle publication par RPC."""
        gestionnaire, _employe = self._gestionnaire_de(self.e_atelier)
        post = self._publication(
            lecture_requise=True, audience="departements",
            department_ids=[(6, 0, [self.dept_bureau.id])])
        post.with_user(self.u_redactrice).action_publier()
        with self.assertRaises(AccessError):
            post.with_user(gestionnaire).action_voir_manquants_equipe()

    def test_le_relais_refuse_sans_lecture_obligatoire(self):
        gestionnaire, _employe = self._gestionnaire_de(self.e_atelier)
        post = self._publication()
        post.with_user(self.u_redactrice).action_publier()
        with self.assertRaises(UserError):
            post.with_user(gestionnaire).action_voir_manquants_equipe()

    def test_peut_relancer_equipe_faux_sans_equipe(self):
        gestionnaire, _employe = self._gestionnaire_de(self.e_atelier)
        post = self._publication(lecture_requise=True)
        post.with_user(self.u_redactrice).action_publier()
        self.assertTrue(post.with_user(gestionnaire).peut_relancer_equipe)
        self.assertFalse(post.with_user(self.u_bureau).peut_relancer_equipe)


@tagged("post_install", "-at_install")
class TestPastilles(TransactionCase):
    """Les couleurs de type, mesurées et non regardées.

    🔴 Une pastille trop pâle ne lève rien : la page s'affiche, le texte est
    là, il est seulement illisible pour une partie des gens. Le contrôle qui
    tranche est un calcul de contraste, pas un coup d'œil.
    """

    SEUIL_AA = 4.5

    @staticmethod
    def _luminance(hexa):
        hexa = hexa.lstrip("#")
        canaux = [int(hexa[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        canaux = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
                  for c in canaux]
        return 0.2126 * canaux[0] + 0.7152 * canaux[1] + 0.0722 * canaux[2]

    @classmethod
    def _contraste(cls, a, b):
        la, lb = cls._luminance(a), cls._luminance(b)
        return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)

    def _feuille(self):
        chemin = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                              "static", "src", "scss", "babillard.scss")
        with io.open(chemin, encoding="utf-8") as f:
            return f.read()

    def test_pastilles_contraste_aa(self):
        feuille = self._feuille()
        bloc = re.search(r"\$o-bf-bab-pastilles:\s*\((.*?)\);", feuille, re.S)
        self.assertTrue(bloc, "la carte des couleurs de type a changé de forme")
        couleurs = re.findall(r'"([a-z]+)":\s*(#[0-9A-Fa-f]{6})', bloc.group(1))
        self.assertTrue(couleurs, "aucune couleur lue dans la feuille")
        types = {code for code, _libelle in
                 self.env["bf.babillard.post"]._fields["type_publication"].selection}
        self.assertEqual({nom for nom, _hexa in couleurs}, types,
                         "chaque type a sa couleur, et rien de plus")
        for nom, hexa in couleurs:
            ratio = self._contraste(hexa, "#FFFFFF")
            self.assertGreaterEqual(
                ratio, self.SEUIL_AA,
                "la pastille %s (%s) rend %.2f:1 sous du texte blanc" % (nom, hexa, ratio))

    def test_l_accent_de_la_maison_ne_porte_pas_de_texte_blanc(self):
        """L'accent brut rend 2,3:1 : il décore, il ne porte pas de texte."""
        # Les commentaires de la feuille NOMMENT l'accent pour dire de ne pas
        # s'en servir : c'est le code qui se mesure, pas la prose.
        code = re.sub(r"//.*", "", self._feuille()).upper()
        self.assertNotIn("#29ABE2", code)
        self.assertLess(self._contraste("#29ABE2", "#FFFFFF"), self.SEUIL_AA)


@tagged("post_install", "-at_install")
class TestCatalogueFrancais(TransactionCase):
    """Le catalogue de traduction, relu comme un fichier.

    🔴 Un script de génération a doublé les chaînes MULTILIGNES : l'avis de
    confidentialité du dialogue de signalement s'est affiché deux fois à
    l'écran, et un `-u` ne l'a pas corrigé, une traduction déjà en base
    n'étant jamais écrasée.
    """

    def _entrees(self):
        import re
        from pathlib import Path
        from odoo.modules.module import get_module_path

        brut = Path(get_module_path("bf_babillard"), "i18n", "fr_CA.po").read_text(
            encoding="utf-8")
        for bloc in brut.split("\n\n")[1:]:
            # ⚠️ Un échappement de trop ici, et le motif saute justement les
            # chaînes MULTILIGNES, les seules qui peuvent doubler.
            m = re.search(r'^msgid ((?:"(?:[^"\\]|\\.)*"\n?)+)^msgstr '
                          r'((?:"(?:[^"\\]|\\.)*"\n?)+)', bloc, re.M)
            if m:
                yield m.group(1).strip(), m.group(2).strip()

    def test_aucune_chaine_doublee(self):
        for msgid, msgstr in self._entrees():
            if msgid == '""':
                continue
            self.assertEqual(
                msgid, msgstr,
                "la traduction française doit recopier la source, une seule fois")

    def test_le_catalogue_n_est_pas_vide(self):
        self.assertGreater(len(list(self._entrees())), 100)


@tagged("post_install", "-at_install")
class TestEnvois(TransactionCase):
    """Ce que le babillard envoie, et à qui.

    Deux envois seulement : une annonce à lire prévient son
    audience (notification Odoo et courriel), et un signalement prévient la
    modération (activité et courriel qui ne dit rien du signalement).
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Users = cls.env["res.users"].with_context(no_reset_password=True)
        g_user = cls.env.ref("base.group_user")
        g_red = cls.env.ref("bf_babillard.group_babillard_redacteur")
        g_mod = cls.env.ref("bf_babillard.group_babillard_moderation")
        cls.dept = cls.env["hr.department"].create({"name": "Quai de chargement"})
        cls.autre_dept = cls.env["hr.department"].create({"name": "Comptabilité"})

        def compte(login, nom, groupes, notif="email"):
            return Users.create({
                "name": nom, "login": login, "email": f"{login}@exemple.test",
                "notification_type": notif,
                "groups_id": [(6, 0, [g.id for g in groupes])]})

        cls.redaction = compte("env_redaction", "Rédaction des envois", [g_user, g_red])
        cls.dans_odoo = compte("env_inbox", "Personne prévenue dans Odoo", [g_user], "inbox")
        cls.par_courriel = compte("env_courriel", "Personne prévenue par courriel", [g_user])
        cls.hors = compte("env_hors", "Personne hors audience", [g_user])
        cls.moderation = compte("env_moderation", "Personne de la modération", [g_user, g_mod])
        cls.moderation2 = compte("env_moderation2", "Seconde personne de la modération",
                                 [g_user, g_mod], "inbox")
        Employe = cls.env["hr.employee"]
        for user, dept in ((cls.dans_odoo, cls.dept), (cls.par_courriel, cls.dept),
                           (cls.hors, cls.autre_dept)):
            Employe.create({"name": user.name, "user_id": user.id, "department_id": dept.id})

    def _mails(self, rec):
        return self.env["mail.mail"].sudo().search(
            [("model", "=", rec._name), ("res_id", "=", rec.id)])

    def _annonce(self, **kw):
        vals = {"name": "Nouvelle procédure du quai", "audience": "departements",
                "department_ids": [(6, 0, [self.dept.id])], "lecture_requise": True,
                "corps_html": "<p>La séquence change lundi.</p>"}
        vals.update(kw)
        return self.env["bf.babillard.post"].with_user(self.redaction).create(vals)

    # --- l'annonce à lire -------------------------------------------------

    def test_annonce_a_lire_ecrit_a_son_audience(self):
        post = self._annonce()
        post.action_publier()
        mails = self._mails(post)
        self.assertEqual(len(mails), 1)
        dest = mails.recipient_ids
        self.assertIn(self.dans_odoo.partner_id, dest)
        self.assertIn(self.par_courriel.partner_id, dest)
        self.assertNotIn(self.hors.partner_id, dest, "hors audience, hors envoi")
        self.assertNotIn(self.redaction.partner_id, dest, "on ne se prévient pas soi-même")
        self.assertIn("Nouvelle procédure du quai", mails.subject)
        self.assertTrue(post.avis_envoye_le)

    def test_le_courriel_porte_le_lien_et_le_texte(self):
        post = self._annonce()
        post.action_publier()
        corps = self._mails(post).body_html
        self.assertIn("model=bf.babillard.post", corps)
        self.assertIn("res_id=%d" % post.id, corps)
        self.assertIn("La séquence change lundi.", corps)

    def test_notification_odoo_pour_qui_la_prefere(self):
        post = self._annonce()
        post.action_publier()
        Notif = self.env["mail.notification"].sudo()
        dom = [("mail_message_id.model", "=", post._name),
               ("mail_message_id.res_id", "=", post.id),
               ("notification_type", "=", "inbox")]
        self.assertTrue(Notif.search(dom + [("res_partner_id", "=", self.dans_odoo.partner_id.id)]))
        self.assertFalse(Notif.search(dom + [("res_partner_id", "=", self.par_courriel.partner_id.id)]))

    def test_jamais_deux_courriels_a_la_meme_personne(self):
        """Qui préfère le courriel ne doit pas recevoir aussi celui de la notification."""
        post = self._annonce()
        post.action_publier()
        compte = sum(1 for m in self._mails(post) for p in m.recipient_ids
                     if p == self.par_courriel.partner_id)
        self.assertEqual(compte, 1)
        self.assertFalse(self.env["mail.notification"].sudo().search([
            ("mail_message_id.model", "=", post._name),
            ("mail_message_id.res_id", "=", post.id),
            ("notification_type", "=", "email")]))

    def test_un_avis_ne_part_qu_une_fois(self):
        post = self._annonce()
        post.action_publier()
        post.action_retirer()
        post.action_remettre_en_brouillon()
        post.action_publier()
        self.assertEqual(len(self._mails(post)), 1)

    def test_une_annonce_ordinaire_ne_previent_personne(self):
        post = self._annonce(lecture_requise=False)
        post.action_publier()
        self.assertFalse(self._mails(post))
        self.assertFalse(post.avis_envoye_le)

    def test_la_file_est_reveillee(self):
        cron = self.env.ref("mail.ir_cron_mail_scheduler_action")
        Trig = self.env["ir.cron.trigger"].sudo()
        avant = Trig.search_count([("cron_id", "=", cron.id)])
        self._annonce().action_publier()
        self.assertGreater(Trig.search_count([("cron_id", "=", cron.id)]), avant)

    def test_la_trace_est_au_fil_de_discussion(self):
        post = self._annonce()
        post.action_publier()
        self.assertTrue(any("Avis de lecture envoyé à 2 personne(s)" in (m.body or "")
                            for m in post.sudo().message_ids),
                        "la trace doit dire à combien de personnes l'avis est parti")

    def test_qui_publie_ne_se_previent_pas(self):
        """Dans l'audience « tous », la rédaction fait partie du public qu'elle vise."""
        post = self._annonce(audience="tous", department_ids=[(5,)])
        post.action_publier()
        mails = self._mails(post)
        self.assertIn(self.par_courriel.partner_id, mails.recipient_ids)
        self.assertNotIn(self.redaction.partner_id, mails.recipient_ids)

    # --- le signalement ---------------------------------------------------

    def _signaler_par(self, user, post, motif="Un commentaire vise une collègue par son nom."):
        self.env["bf.babillard.signalement.assistant"].with_user(user).create(
            {"post_ref": post.id, "motif": motif}).action_envoyer()
        return self.env["bf.babillard.signalement"].sudo().search(
            [("post_id", "=", post.id)], order="id desc", limit=1)

    def _signaler(self, motif="Un commentaire vise une collègue par son nom."):
        post = self._annonce(name="Titre confidentiel du quai", lecture_requise=False)
        post.action_publier()
        return self._signaler_par(self.par_courriel, post, motif)

    def _activites(self, sig):
        return self.env["mail.activity"].sudo().search(
            [("res_model", "=", sig._name), ("res_id", "=", sig.id)])

    def _annonce_de_la_moderation(self):
        post = self._annonce(name="Annonce signée par la modération", audience="tous",
                             department_ids=[(5,)], lecture_requise=False,
                             auteur_user_id=self.moderation.id)
        post.action_publier()
        return post

    def test_signalement_pose_une_activite_par_personne_de_la_moderation(self):
        sig = self._signaler()
        activites = self.env["mail.activity"].sudo().search(
            [("res_model", "=", sig._name), ("res_id", "=", sig.id)])
        self.assertIn(self.moderation, activites.user_id)
        self.assertIn(self.moderation2, activites.user_id)
        self.assertNotIn(self.redaction, activites.user_id)

    def test_signalement_ecrit_a_la_moderation_et_a_elle_seule(self):
        sig = self._signaler()
        mails = self._mails(sig)
        self.assertEqual(len(mails), 1,
                         "un seul courriel : l'avis d'assignation d'activité ne doit pas partir")
        self.assertIn(self.moderation.partner_id, mails.recipient_ids)
        self.assertNotIn(self.redaction.partner_id, mails.recipient_ids)
        self.assertNotIn(self.par_courriel.partner_id, mails.recipient_ids)
        # ⚠️ La rédaction et la personne qui signale sont déjà écartées pour une
        # AUTRE raison (autrice du contenu, qui signale). Sans ces deux-là, un
        # envoi à tout le personnel passait l'essai : la mutation l'a montré.
        self.assertNotIn(self.hors.partner_id, mails.recipient_ids)
        self.assertNotIn(self.dans_odoo.partner_id, mails.recipient_ids)

    def test_aucun_avis_d_assignation_d_activite(self):
        """🔴 L'avis d'Odoo part en envoi immédiat puis s'efface de la file : il ne
        se voit pas dans `mail.mail`. On le cherche donc au message qu'il laisse."""
        sig = self._signaler()
        self.assertFalse(self.env["mail.message"].sudo().search([
            ("model", "=", sig._name), ("res_id", "=", sig.id),
            ("message_type", "=", "user_notification")]))

    def test_qui_signale_depuis_la_moderation_ne_se_previent_pas(self):
        post = self._annonce(name="Annonce pour tout le monde", audience="tous",
                             department_ids=[(5,)], lecture_requise=False)
        post.action_publier()
        sig = self._signaler_par(self.moderation2, post, "Propos déplacé.")
        activites = self.env["mail.activity"].sudo().search(
            [("res_model", "=", sig._name), ("res_id", "=", sig.id)])
        self.assertIn(self.moderation, activites.user_id)
        self.assertNotIn(self.moderation2, activites.user_id)
        self.assertNotIn(self.moderation2.partner_id, self._mails(sig).recipient_ids)

    def test_le_courriel_de_signalement_ne_dit_rien(self):
        sig = self._signaler()
        mail = self._mails(sig)
        texte = (mail.subject or "") + (mail.body_html or "")
        self.assertNotIn("Un commentaire vise une collègue", texte, "le motif a fuité")
        self.assertNotIn(self.par_courriel.name, texte, "le nom de qui signale a fuité")
        self.assertNotIn("Titre confidentiel du quai", texte, "la publication visée a fuité")

    def test_la_personne_visee_ne_recoit_pas_le_signalement(self):
        """🔴 Qui reçoit la plainte contre soi apprend le motif et qui s'est plaint."""
        sig = self._signaler_par(self.par_courriel, self._annonce_de_la_moderation())
        self.assertNotIn(self.moderation, self._activites(sig).user_id)
        self.assertIn(self.moderation2, self._activites(sig).user_id)
        self.assertNotIn(self.moderation.partner_id, self._mails(sig).recipient_ids)
        self.assertNotIn(
            sig, self.env["bf.babillard.signalement"].with_user(self.moderation).search([]))

    def test_sans_autre_moderation_le_signalement_remonte_a_l_administration(self):
        self.env.ref("bf_babillard.group_babillard_moderation").write(
            {"users": [(6, 0, [self.moderation.id])]})
        sig = self._signaler_par(self.par_courriel, self._annonce_de_la_moderation())
        admin = self.env.ref("base.user_admin")
        self.assertTrue(sig.escalade)
        self.assertIn(admin, self._activites(sig).user_id)
        self.assertNotIn(self.moderation, self._activites(sig).user_id)
        Sig = self.env["bf.babillard.signalement"]
        self.assertIn(sig, Sig.with_user(admin).search([]))
        self.assertNotIn(sig, Sig.with_user(self.moderation).search([]))

    def test_le_courriel_de_signalement_ne_part_pas_au_nom_de_qui_signale(self):
        """🔴 Sans adresse de société, le gabarit prenait celle de l'utilisateur courant."""
        self.env.company.email = False
        mail = self._mails(self._signaler())
        self.assertTrue(mail)
        self.assertNotIn(self.par_courriel.email, mail.email_from or "")
        self.assertNotIn(self.par_courriel.name, mail.email_from or "")
        self.assertNotEqual(mail.author_id, self.par_courriel.partner_id)

    def test_une_lecture_cochee_apres_publication_previent_une_fois(self):
        post = self._annonce(lecture_requise=False)
        post.action_publier()
        self.assertFalse(self._mails(post))
        post.write({"lecture_requise": True})
        self.assertEqual(len(self._mails(post)), 1)
        post.write({"lecture_requise": False})
        post.write({"lecture_requise": True})
        self.assertEqual(len(self._mails(post)), 1)

    def test_une_annonce_creee_publiee_previent(self):
        post = self._annonce(state="publie")
        self.assertEqual(len(self._mails(post)), 1)
        self.assertTrue(post.date_publication)

    def test_un_signalement_sans_destinataire_est_conserve_et_dit(self):
        """Personne pour le recevoir : le dossier reste, et le dialogue le dit.

        🔴 Le dialogue promettait « parti vers la personne désignée » même quand
        la seule personne de la modération était celle que le contenu visait.
        """
        societe = self.env["res.company"].create({"name": "Société sans recours"})
        Users = self.env["res.users"].with_context(no_reset_password=True)
        groupes = [self.env.ref("base.group_user").id,
                   self.env.ref("bf_babillard.group_babillard_moderation").id,
                   self.env.ref("bf_babillard.group_babillard_redacteur").id,
                   self.env.ref("base.group_system").id]
        seule = Users.create({
            "name": "Seule personne désignée", "login": "envois_seule",
            "company_id": societe.id, "company_ids": [(6, 0, [societe.id])],
            "groups_id": [(6, 0, groupes)]})
        qui_signale = Users.create({
            "name": "Personne qui signale", "login": "envois_signale",
            "company_id": societe.id, "company_ids": [(6, 0, [societe.id])],
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])]})
        post = self.env["bf.babillard.post"].with_user(seule).create({
            "name": "Annonce de la société sans recours", "audience": "tous",
            "company_id": societe.id, "corps_html": "<p>Consigne.</p>"})
        post.with_user(seule).action_publier()
        assistant = self.env["bf.babillard.signalement.assistant"].with_user(
            qui_signale).create({"post_ref": post.id, "motif": "Personne pour le recevoir."})
        action = assistant.action_envoyer()
        sig = self.env["bf.babillard.signalement"].sudo().search(
            [("post_id", "=", post.id)], limit=1)
        self.assertTrue(sig.sans_destinataire)
        self.assertFalse(self._mails(sig))
        self.assertEqual(action["params"]["type"], "warning")
