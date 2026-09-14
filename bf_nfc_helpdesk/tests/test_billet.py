"""Signaler un problème : une phrase, un billet rempli, et rien de plus."""
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestBillet(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param("bf_nfc.fenetre_doublon_secondes", "0")
        # Un interne SANS droit sur le soutien : c'est le cas qui compte.
        cls.technicien = new_test_user(cls.env, login="billet-technicien",
                                       groups="base.group_user,base.group_partner_manager")
        cls.client = cls.env["res.partner"].create({"name": "Client de l'imprimante", "is_company": True})
        cls.pastille = cls.env["bf.nfc.tag"].create({
            "name": "Imprimante", "place": "Imprimante du 2e",
            "gesture_id": cls.env.ref("bf_nfc_helpdesk.gesture_ticket").id,
            "res_model": "res.partner", "res_id": cls.client.id,
        })

    def _billets(self):
        return self.env["helpdesk.ticket"].search([("partner_id", "=", self.client.id)])

    def test_sans_texte_une_question_et_aucun_billet(self):
        r = self.pastille.with_user(self.technicien).taper("app")
        self.assertEqual(r["statut"], "choice")
        self.assertEqual(r["choix"][0]["saisie"], "texte")
        self.assertFalse(self._billets())

    def test_la_phrase_cree_le_billet_rempli(self):
        r = self.pastille.with_user(self.technicien).taper("app", choix="signaler", texte="Bourrage papier")
        self.assertEqual(r["statut"], "ok", r.get("message"))
        billet = self._billets()
        self.assertEqual(len(billet), 1)
        self.assertIn("Bourrage papier", billet.name)
        self.assertIn("Imprimante du 2e", billet.description)
        self.assertIn(self.technicien.name, billet.description)
        self.assertEqual(billet.channel_id, self.env.ref("bf_nfc_helpdesk.channel_pastille"))
        # `helpdesk_mgmt` ouvre la lecture des billets à tout interne : le lien est rendu.
        self.assertEqual(r["url"], "/mail/view?model=helpdesk.ticket&res_id=%s" % billet.id)

    def test_choisir_sans_ecrire_est_refuse(self):
        r = self.pastille.with_user(self.technicien).taper("app", choix="signaler")
        self.assertEqual(r["statut"], "refused")
        self.assertFalse(self._billets())

    def test_texte_fixe_d_un_menu_sans_question(self):
        fixe = self.env["bf.nfc.tag"].create({
            "name": "Papier", "gesture_id": self.env.ref("bf_nfc_helpdesk.gesture_ticket").id,
            "res_model": "res.partner", "res_id": self.client.id,
            "params": '{"texte_fixe": "Plus de papier"}',
        })
        r = fixe.with_user(self.technicien).taper("app")
        self.assertEqual(r["statut"], "ok", r.get("message"))
        self.assertIn("Plus de papier", self._billets().name)

    def test_la_fiche_prete_son_client(self):
        projet = self.env["project.project"].create({"name": "Parc d'impression", "partner_id": self.client.id}) \
            if "project.project" in self.env else None
        if not projet:
            self.skipTest("project absent")
        pastille = self.env["bf.nfc.tag"].create({
            "name": "Parc", "gesture_id": self.env.ref("bf_nfc_helpdesk.gesture_ticket").id,
            "res_model": "project.project", "res_id": projet.id,
        })
        gestion = new_test_user(self.env, login="billet-gestion-projet",
                                groups="base.group_user,project.group_project_manager")
        r = pastille.with_user(gestion).taper("app", choix="signaler", texte="Toner vide")
        self.assertEqual(r["statut"], "ok", r.get("message"))
        self.assertTrue(self._billets().filtered(lambda b: "Toner vide" in b.name))

    def test_le_client_qui_signale_suit_son_billet(self):
        """⚠️ `sudo()` garde l'utilisateur : la personne qui signale devient abonnée
        du billet, le lit (règle portail « mes billets ») et en reçoit les suites."""
        portail = new_test_user(self.env, login="billet-portail", groups="base.group_portal")
        pastille = self.env["bf.nfc.tag"].create({
            "name": "Borne", "gesture_id": self.env.ref("bf_nfc_helpdesk.gesture_ticket").id,
            "res_model": "res.partner", "res_id": portail.partner_id.id,
            "params": '{"texte_fixe": "Borne en panne"}',
        })
        r = pastille.with_user(portail).taper("app")
        self.assertEqual(r["statut"], "ok", r.get("message"))
        billet = self.env["helpdesk.ticket"].search([("partner_id", "=", portail.partner_id.id)])
        self.assertIn(portail.partner_id, billet.message_partner_ids)
        self.assertTrue(r["url"])

    def test_le_texte_est_echappe(self):
        self.pastille.with_user(self.technicien).taper("app", choix="signaler", texte="<script>x</script>")
        self.assertNotIn("<script>", self._billets().description)
