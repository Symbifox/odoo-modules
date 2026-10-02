# -*- coding: utf-8 -*-
"""Un avis appartient à la personne nommée.

Avant 1.1.0, n'importe quel utilisateur interne pouvait approuver ou refuser à
la place d'un approbateur nommé (et le fil disait alors « <approbateur> a
approuvé »), écrire l'avis directement par RPC, ou retirer un approbateur
requis qui avait refusé, puis publier. Chaque essai joue le geste dans le
rôle de quelqu'un qui n'a pas le droit de le faire, puis dans celui de qui
l'a : un essai joué en superutilisateur ne prouve rien sur des droits.
"""
from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged

USAGER = "base.group_user,project_knowledge_matrix.group_document_user"
GESTION = "base.group_user,project_knowledge_matrix.group_document_manager"


@tagged("post_install", "-at_install")
class TestDroitsApprobation(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.responsable = new_test_user(
            cls.env, "resp.essai", groups=USAGER, name="Responsable")
        cls.tremblay = new_test_user(
            cls.env, "tremblay.essai", groups=USAGER, name="M. Tremblay")
        cls.couture = new_test_user(
            cls.env, "couture.essai", groups=USAGER, name="Mme Couture")
        cls.intrus = new_test_user(
            cls.env, "intrus.essai", groups=USAGER, name="Intrus")
        cls.gestionnaire = new_test_user(
            cls.env, "gest.essai", groups=GESTION, name="Gestionnaire")
        cls.etranger = new_test_user(
            cls.env, "etranger.essai", groups=USAGER, name="Étranger")
        cls.projet = cls.env["project.project"].create(
            {"name": "Essai des droits"})
        # Tous suivent le projet, sauf l'étranger : c'est la règle d'accès
        # du module hôte sur les versions.
        cls.projet.message_subscribe(partner_ids=(
            cls.responsable | cls.tremblay | cls.couture | cls.intrus
            | cls.gestionnaire).partner_id.ids)
        matrice = cls.env["project.knowledge.matrix"].create({
            "name": "Matrice des droits", "project_id": cls.projet.id})
        type_doc = cls.env["project.document.type"].create({
            "name": "Politique", "code": "POLD"})
        cls.doc = cls.env["project.document"].create({
            "name": "Politique d'accès", "code": "POL-026",
            "matrix_id": matrice.id, "type_id": type_doc.id,
            "project_id": cls.projet.id, "owner_id": cls.responsable.id})
        cls.version = cls.env["project.document.version"].create({
            "document_id": cls.doc.id, "version_number": "2.0"})

    def _ligne(self, utilisateur, requis=True):
        return self.env["project.document.approver"].create({
            "version_id": self.version.id, "user_id": utilisateur.id,
            "requis": requis})

    def _messages(self, fragment):
        return self.doc.message_ids.filtered(
            lambda m: fragment in (m.body or ""))

    # ------------------------------------------ à la place de quelqu'un
    def test_un_tiers_ne_peut_pas_approuver_a_la_place(self):
        ligne = self._ligne(self.tremblay)
        with self.assertRaises(AccessError):
            ligne.with_user(self.intrus).action_approuver()
        self.assertEqual(ligne.avis, "attente")
        self.assertFalse(self._messages("a approuvé"))

    def test_un_tiers_ne_peut_pas_refuser_a_la_place(self):
        ligne = self._ligne(self.tremblay)
        for qui in (self.intrus, self.responsable, self.gestionnaire):
            with self.assertRaises(AccessError):
                ligne.with_user(qui).write({"commentaire": "Je bloque."})
        # Même avec un motif déjà saisi par l'approbateur, le bouton ne
        # parle pas pour lui.
        ligne.with_user(self.tremblay).write(
            {"commentaire": "À relire."})
        with self.assertRaises(AccessError):
            ligne.with_user(self.intrus).action_refuser()
        self.assertEqual(ligne.avis, "attente")

    def test_ecrire_l_avis_directement_est_refuse(self):
        """Ni le tiers, ni le gestionnaire, ni l'approbateur lui-même : un
        avis se donne par les boutons, qui datent et le disent au fil."""
        ligne = self._ligne(self.tremblay)
        for qui in (self.intrus, self.gestionnaire, self.tremblay):
            with self.assertRaises(AccessError):
                ligne.with_user(qui).write({"avis": "approuve"})
        with self.assertRaises(AccessError):
            ligne.with_user(self.intrus).write(
                {"date_avis": fields.Datetime.now()})
        with self.assertRaises(AccessError):
            self.version.with_user(self.intrus).write({"approver_ids": [
                Command.update(ligne.id, {"avis": "approuve"})]})
        self.assertEqual(ligne.avis, "attente")
        self.assertFalse(ligne.date_avis)

    def test_creer_une_ligne_deja_approuvee_est_refuse(self):
        with self.assertRaises(AccessError):
            self.version.with_user(self.responsable).write({"approver_ids": [
                Command.create({"user_id": self.tremblay.id,
                                "avis": "approuve"})]})
        self.assertFalse(self.version.approver_ids)

    # ------------------------------------------- composer le tour de table
    def test_un_tiers_ne_retire_pas_un_approbateur_requis(self):
        ligne = self._ligne(self.tremblay)
        with self.assertRaises(AccessError):
            ligne.with_user(self.intrus).unlink()
        with self.assertRaises(AccessError):
            self.version.with_user(self.intrus).write(
                {"approver_ids": [Command.delete(ligne.id)]})
        with self.assertRaises(AccessError):
            ligne.with_user(self.intrus).write({"requis": False})
        self.assertTrue(ligne.exists())
        self.assertTrue(ligne.requis)

    def test_un_tiers_n_ajoute_pas_d_approbateur(self):
        with self.assertRaises(AccessError):
            self.env["project.document.approver"].with_user(
                self.intrus).create({"version_id": self.version.id,
                                     "user_id": self.couture.id})

    def test_le_responsable_et_le_gestionnaire_composent(self):
        """Une ligne en attente se compose librement, et ce qui affaiblit le
        verrou (retrait, avis rendu facultatif) laisse une trace."""
        for qui in (self.responsable, self.gestionnaire):
            Lignes = self.env["project.document.approver"].with_user(qui)
            ligne = Lignes.create({"version_id": self.version.id,
                                   "user_id": self.tremblay.id})
            ligne.write({"user_id": self.couture.id})
            ligne.write({"user_id": self.tremblay.id})
            ligne.write({"requis": False, "sequence": 5})
            ligne.unlink()
        self.assertEqual(
            len(self._messages("remplacé M. Tremblay par Mme Couture")), 2)
        self.assertEqual(len(self._messages("retiré M. Tremblay")), 2)
        self.assertEqual(len(self._messages("facultatif")), 2)

    def test_supprimer_la_version_emporte_ses_lignes(self):
        """Le gestionnaire qui supprime une version entière n'est pas bloqué
        par les avis qu'elle porte : la cascade est celle de la base."""
        ligne = self._ligne(self.tremblay)
        ligne.with_user(self.tremblay).action_approuver()
        self.version.with_user(self.gestionnaire).unlink()
        self.assertFalse(ligne.exists())

    def test_un_avis_consigne_ne_se_retire_plus(self):
        """Le scénario du défaut : retirer celui qui a refusé, puis publier."""
        ligne = self._ligne(self.tremblay)
        ligne.with_user(self.tremblay).write(
            {"commentaire": "La section 3 est fausse."})
        ligne.with_user(self.tremblay).action_refuser()
        for qui in (self.responsable, self.gestionnaire):
            with self.assertRaises(UserError):
                ligne.with_user(qui).unlink()
            with self.assertRaises(UserError):
                ligne.with_user(qui).write({"requis": False})
            with self.assertRaises(UserError):
                ligne.with_user(qui).write({"user_id": self.couture.id})
        self.assertTrue(ligne.exists())
        with self.assertRaises(UserError):
            self.version.with_user(self.responsable).action_release()
        self.assertEqual(self.version.state, "draft")

    # ------------------------------------------------- la personne nommée
    def test_l_approbateur_se_prononce_et_le_fil_dit_vrai(self):
        ligne = self._ligne(self.tremblay)
        # Le client web renvoie parfois une valeur inchangée avec le motif :
        # un geste vide ne se refuse pas.
        ligne.with_user(self.tremblay).write(
            {"commentaire": "Lu.", "sequence": ligne.sequence})
        ligne.with_user(self.tremblay).action_approuver()
        self.assertEqual(ligne.avis, "approuve")
        self.assertTrue(ligne.date_avis)
        message = self._messages("M. Tremblay a approuvé")
        self.assertEqual(len(message), 1)
        self.assertEqual(message.author_id, self.tremblay.partner_id)
        # Il peut changer d'avis : c'est encore le sien.
        ligne.with_user(self.tremblay).write(
            {"commentaire": "Tout compte fait, non."})
        ligne.with_user(self.tremblay).action_refuser()
        self.assertEqual(ligne.avis, "refuse")

    def test_un_flux_serveur_qui_consigne_le_dit(self):
        """Un code serveur en sudo peut consigner un avis reçu ailleurs ; le
        fil nomme alors qui l'a consigné, et pour qui."""
        ligne = self._ligne(self.tremblay)
        ligne.with_user(self.gestionnaire).sudo().action_approuver()
        self.assertEqual(ligne.avis, "approuve")
        message = self._messages("au nom de M. Tremblay")
        self.assertEqual(len(message), 1)
        self.assertEqual(message.author_id, self.gestionnaire.partner_id)
        self.assertFalse(self._messages("M. Tremblay a approuvé"))

    def test_les_boutons_ne_parlent_qu_a_l_approbateur(self):
        ligne = self._ligne(self.tremblay)
        self.assertTrue(ligne.with_user(self.tremblay).est_mon_avis)
        self.assertFalse(ligne.with_user(self.intrus).est_mon_avis)
        version = self.version
        self.assertTrue(
            version.with_user(self.responsable).approbateurs_modifiables)
        self.assertTrue(
            version.with_user(self.gestionnaire).approbateurs_modifiables)
        self.assertFalse(
            version.with_user(self.intrus).approbateurs_modifiables)
        self.assertFalse(
            version.with_user(self.tremblay).approbateurs_modifiables)

    def test_les_lignes_suivent_la_visibilite_de_la_version(self):
        ligne = self._ligne(self.tremblay)
        Lignes = self.env["project.document.approver"]
        self.assertFalse(Lignes.with_user(self.etranger).search(
            [("version_id", "=", self.version.id)]))
        self.assertEqual(Lignes.with_user(self.intrus).search(
            [("version_id", "=", self.version.id)]), ligne)

    # ---------------------------------------- le verrou, de bout en bout
    def test_la_publication_reste_verrouillee_de_bout_en_bout(self):
        a = self._ligne(self.tremblay)
        b = self._ligne(self.couture)
        with self.assertRaises(AccessError):
            a.with_user(self.intrus).action_approuver()
        with self.assertRaises(AccessError):
            b.with_user(self.intrus).write({"avis": "approuve"})
        with self.assertRaises(AccessError):
            b.with_user(self.intrus).unlink()
        with self.assertRaises(UserError):
            self.version.with_user(self.intrus).action_release()
        a.with_user(self.tremblay).action_approuver()
        with self.assertRaises(UserError):
            self.version.with_user(self.responsable).action_release()
        b.with_user(self.couture).action_approuver()
        self.version.with_user(self.responsable).action_release()
        self.assertEqual(self.version.state, "released")
