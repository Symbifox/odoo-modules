"""Les petits gestes de la vague 3.

Aucun n'est une fonction : ce sont des raccourcis d'un travail qu'on faisait
déjà à la main. Ce qui les rend intéressants à éprouver, ce sont leurs REFUS :
la corbeille qui refuse une ligne classée sur une fiche, le rappel de pièce
jointe qui se tait quand il y en a une, le ménage qui épargne les questions.
"""
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import MobileApiCase


@tagged("post_install", "-at_install")
class TestPetitsGestes(MobileApiCase):

    # -- corbeille -------------------------------------------------------
    def test_la_corbeille_sort_la_ligne_de_toute_liste(self):
        ligne = self.with_attachment.with_user(self.owner)
        ligne.action_trash()
        self.assertTrue(ligne.is_handled)
        self.assertFalse(ligne.active)

    def test_la_corbeille_refuse_une_ligne_classee_sur_une_fiche(self):
        """⚠️ Le message vit dans le chatter de la fiche : le jeter d'ici
        laisserait la fiche avec un message dont la ligne a disparu."""
        self.inbound.sudo().write({
            "res_model": "res.partner", "res_id": self.partner.id})
        with self.assertRaises(UserError):
            self.inbound.with_user(self.owner).action_trash()
        self.assertTrue(self.inbound.active)

    # -- tout marquer comme lu -------------------------------------------
    def test_tout_marquer_comme_lu_ne_traite_rien(self):
        """Marquer lu et traiter sont deux axes, et c'est tout l'intérêt."""
        avant = self.inbound.is_handled
        res = self.env["bf.email"].with_user(self.owner).inbox_mark_folder_read(
            "inbox")
        self.assertGreaterEqual(res["marked"], 1)
        self.inbound.invalidate_recordset()
        self.assertEqual(self.inbound.status, "read")
        self.assertEqual(self.inbound.is_handled, avant)

    def test_il_ne_touche_pas_la_boite_d_un_collegue(self):
        self.env["bf.email"].with_user(self.owner).inbox_mark_folder_read("inbox")
        self.foreign.invalidate_recordset()
        self.assertEqual(self.foreign.status, "new")

    # -- report annulé ----------------------------------------------------
    def test_annuler_un_report_ramene_la_ligne(self):
        ligne = self.inbound.with_user(self.owner)
        ligne.write({"is_handled": True, "snoozed_until": "2030-01-01 08:00:00"})
        ligne.action_unsnooze()
        self.assertFalse(ligne.snoozed_until)
        self.assertFalse(ligne.is_handled)

    # -- contact ----------------------------------------------------------
    def test_un_contact_connu_se_rattache(self):
        self.inbound.sudo().write({"partner_id": False})
        self.inbound.with_user(self.owner).action_link_partner()
        self.inbound.invalidate_recordset()
        self.assertEqual(self.inbound.partner_id, self.partner)

    def test_un_contact_inconnu_ouvre_la_fiche_a_creer(self):
        self.with_attachment.sudo().write({"partner_id": False})
        action = self.with_attachment.with_user(self.owner).action_link_partner()
        self.assertEqual(action["res_model"], "res.partner")
        self.assertEqual(action["context"]["default_email"],
                         "rapports@fournisseur.test")

    # -- règle depuis le courriel ----------------------------------------
    def test_la_regle_nait_avec_sa_condition(self):
        action = self.inbound.with_user(self.owner).action_create_rule_here()
        self.assertEqual(action["res_model"], "bf.email.rule")
        condition = action["context"]["default_condition_ids"][0][2]
        self.assertEqual(condition["field_name"], "email_from")
        self.assertEqual(condition["value"], "client@acme.test")

    # -- rappel de pièce jointe ------------------------------------------
    def _cible(self):
        """⚠️ Une TÂCHE et non une fiche contact : Odoo refuse à un employé
        sans « Création de contacts » d'écrire dans le chatter d'un
        `res.partner`, et le test échouerait sur l'accès plutôt que sur ce
        qu'il prétend éprouver.

        ⚠️ Et créée à CHAQUE test, pas retenue sur la classe : chaque test
        roule dans sa propre transaction annulée à la fin, donc une fiche
        gardée d'un test à l'autre n'existe déjà plus (`MissingError`).
        """
        projet = self.env["project.project"].create({"name": "Essai 25653"})
        return self.env["project.task"].create({
            "name": "Fil d'essai", "project_id": projet.id,
        })

    def _composeur(self, body, attachments=None):
        cible = self._cible()
        return self.env["mail.compose.message"].with_user(self.owner).create({
            "model": "project.task",
            "res_ids": repr([cible.id]),
            "subject": "Essai",
            "body": body,
            "attachment_ids": [(6, 0, attachments or [])],
        })

    def test_le_rappel_se_declenche_sur_ci_joint(self):
        composeur = self._composeur("<p>Bonjour, ci-joint le rapport.</p>")
        self.assertTrue(composeur.bf_attachment_hint)

    def test_il_se_tait_quand_la_piece_est_la(self):
        piece = self.env["ir.attachment"].create({
            "name": "rapport.pdf", "datas": "dGVzdA==",
        })
        composeur = self._composeur("<p>Ci-joint le rapport.</p>", [piece.id])
        self.assertFalse(composeur.bf_attachment_hint)

    def test_il_ne_crie_pas_pour_rien(self):
        """⚠️ « nous avons joint nos efforts » ne doit rien déclencher : un
        rappel qui se trompe s'apprend à ignorer."""
        composeur = self._composeur("<p>Nous avons joint nos efforts.</p>")
        self.assertFalse(composeur.bf_attachment_hint)

    def test_please_find_attached_aussi(self):
        composeur = self._composeur("<p>Please find attached the report.</p>")
        self.assertTrue(composeur.bf_attachment_hint)

    # -- envoyer et classer -----------------------------------------------
    def test_envoyer_et_classer_sort_la_ligne_d_origine(self):
        self.inbound.sudo().write({"is_handled": False})
        cible = self._cible()
        composeur = self.env["mail.compose.message"].with_user(
            self.owner
        ).with_context(
            bf_handle_source_ids=[self.inbound.id]
        ).create({
            "model": "project.task",
            "res_ids": repr([cible.id]),
            "subject": "Réponse",
            "body": "<p>Voilà.</p>",
        })
        composeur._action_send_mail()
        self.inbound.invalidate_recordset()
        self.assertTrue(self.inbound.is_handled)

    def test_un_composeur_ordinaire_ne_traite_rien(self):
        """Sans le contexte, rien ne bouge : un composeur de chatter ne doit
        pas vider la boîte derrière le dos de qui écrit."""
        self.inbound.sudo().write({"is_handled": False})
        composeur = self._composeur("<p>Bonjour.</p>")
        composeur._action_send_mail()
        self.inbound.invalidate_recordset()
        self.assertFalse(self.inbound.is_handled)

    # -- ménage ------------------------------------------------------------
    def test_le_menage_compte_avant_de_faire(self):
        wiz = self.env["bf.email.cleanup"].with_user(self.owner).create({
            "days": 1, "keep_questions": False})
        self.assertGreaterEqual(wiz.match_count, 1)
        self.assertIn("@", wiz.sample)

    def test_il_epargne_les_questions(self):
        self.assertTrue(self.inbound.is_question,
                        "le jeu d'essai porte bien une question")
        wiz = self.env["bf.email.cleanup"].with_user(self.owner).create({
            "days": 1, "keep_questions": True})
        cibles = wiz._targets()
        self.assertNotIn(self.inbound.id, cibles.ids)

    def test_il_traite_sans_supprimer(self):
        wiz = self.env["bf.email.cleanup"].with_user(self.owner).create({
            "days": 1, "keep_questions": False})
        cibles = wiz._targets()
        self.assertTrue(cibles)
        wiz.action_clean()
        for ligne in cibles:
            ligne.invalidate_recordset()
            self.assertTrue(ligne.is_handled)
            self.assertTrue(ligne.active, "rien n'est supprimé")

    def test_il_ne_voit_que_ma_boite(self):
        wiz = self.env["bf.email.cleanup"].with_user(self.owner).create({
            "days": 1, "keep_questions": False})
        self.assertNotIn(self.foreign.id, wiz._targets().ids)
