"""Le prêt par pastille : un tapotement dans les cas courants, une question sinon."""
from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestPret(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param("bf_nfc.fenetre_doublon_secondes", "0")
        cls.alice = new_test_user(cls.env, login="pret-alice", groups="base.group_user")
        cls.bruno = new_test_user(cls.env, login="pret-bruno", groups="base.group_user")
        cls.portable = cls.env["bf.nfc.equipment"].create({
            "name": "Portable de prêt", "place": "Armoire de l'accueil", "max_days": 3})
        cls.pastille = cls.env["bf.nfc.tag"].create({
            "name": "Portable", "gesture_id": cls.env.ref("bf_nfc_loan.gesture_loan").id,
            "res_model": "bf.nfc.equipment", "res_id": cls.portable.id,
        })

    def test_libre_il_est_pris_puis_rendu_sans_bouton(self):
        r = self.pastille.with_user(self.alice).taper("app")
        self.assertEqual(r["statut"], "ok", r.get("message"))
        self.assertEqual(self.portable.holder_id, self.alice)
        r = self.pastille.with_user(self.alice).taper("app")
        self.assertEqual(r["statut"], "ok")
        self.assertIn("Armoire de l'accueil", r["message"])
        self.assertFalse(self.portable.holder_id)
        self.assertEqual(len(self.portable.loan_ids), 1)
        self.assertTrue(self.portable.loan_ids.date_end)

    def test_chez_un_autre_une_question_puis_la_reprise(self):
        self.pastille.with_user(self.alice).taper("app")
        question = self.pastille.with_user(self.bruno).taper("app")
        self.assertEqual(question["statut"], "choice")
        self.assertIn(self.alice.name, question["message"])
        self.assertEqual(self.portable.holder_id, self.alice, "Une question ne change rien.")
        r = self.pastille.with_user(self.bruno).taper("app", choix="reprendre")
        self.assertEqual(r["statut"], "ok", r.get("message"))
        self.assertEqual(self.portable.holder_id, self.bruno)
        ancien = self.portable.loan_ids.filtered(lambda l: l.user_id == self.alice)
        self.assertEqual(ancien.closed_by_id, self.bruno)

    def test_rendre_ce_qu_on_n_a_pas_est_refuse(self):
        menu_rendre = self.env["bf.nfc.tag"].create({
            "name": "Rendre", "gesture_id": self.env.ref("bf_nfc_loan.gesture_loan").id,
            "res_model": "bf.nfc.equipment", "res_id": self.portable.id,
            "params": '{"sens": "rendre"}',
        })
        r = menu_rendre.with_user(self.alice).taper("app")
        self.assertEqual(r["statut"], "refused")
        self.assertIn("rien à rendre", r["message"])

    def test_pris_en_differe_garde_son_heure(self):
        quand = fields.Datetime.now() - timedelta(hours=5)
        r = self.pastille.with_user(self.alice).taper("app", quand=quand.strftime("%Y-%m-%dT%H:%M:%SZ"))
        self.assertEqual(r["statut"], "ok", r.get("message"))
        self.assertEqual(self.portable.since.replace(microsecond=0), quand.replace(microsecond=0))
        self.assertTrue(self.portable.current_loan_id.offline)

    def test_la_pastille_signee_ne_prete_pas_au_compte_designe(self):
        r = self.pastille.with_user(self.alice).taper("signed")
        self.assertEqual(r["statut"], "refused")

    def test_rappel_une_seule_fois_apres_le_delai(self):
        self.pastille.with_user(self.alice).taper("app")
        self.portable.current_loan_id.date_start = fields.Datetime.now() - timedelta(days=4)
        self.env["bf.nfc.equipment"]._cron_rappels()
        self.env["bf.nfc.equipment"]._cron_rappels()
        activites = self.env["mail.activity"].search([
            ("res_model", "=", "bf.nfc.equipment"), ("res_id", "=", self.portable.id)])
        self.assertEqual(len(activites), 1)
        self.assertEqual(activites.user_id, self.alice)


@tagged("post_install", "-at_install")
class TestPretEnLot(TransactionCase):

    def test_les_equipements_recoivent_leur_pastille_de_pret(self):
        chef = new_test_user(self.env, login="pret-lot-chef", groups="base.group_user,bf_nfc.group_nfc_manager")
        portables = self.env["bf.nfc.equipment"].with_user(chef).create(
            [{"name": "Portable 1", "place": "Armoire"}, {"name": "Portable 2"}])
        lot = self.env["bf.nfc.tag.lot"].with_user(chef).with_context(
            portables.action_creer_pastilles()["context"]).create({})
        self.assertEqual(lot.gesture_id.kind, "loan")
        lot.action_creer()
        self.assertEqual(portables.mapped("nfc_tag_count"), [1, 1])


@tagged("post_install", "-at_install")
class TestRegistreDesCadenas(TransactionCase):
    """RSST, art. 205 : cadenas sans nom remis à une personne qui se nomme."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param("bf_nfc.fenetre_doublon_secondes", "0")
        cls.coordination = new_test_user(cls.env, login="cadenas-coord", groups="base.group_user")
        cls.cadenas = cls.env["bf.nfc.equipment"].create(
            {"name": "Cadenas 7", "reference": "CAD-007", "registre_externe": True})
        cls.pastille = cls.env["bf.nfc.tag"].create({
            "name": "Cadenas 7", "gesture_id": cls.env.ref("bf_nfc_loan.gesture_loan").id,
            "res_model": "bf.nfc.equipment", "res_id": cls.cadenas.id,
        })

    def _taper(self, **kw):
        return self.pastille.with_user(self.coordination).taper("app", **kw)

    def test_remettre_demande_l_identite_puis_la_note(self):
        r = self._taper()
        self.assertEqual([c["cle"] for c in r["formulaire"]], ["nom", "telephone", "employeur"])
        self.assertFalse(self.cadenas.loan_ids)
        refus = self._taper(choix="remettre", reponses={"nom": "Luc Tremblay"})
        self.assertEqual(refus["statut"], "refused")
        self.assertIn("Téléphone", refus["message"])
        r = self._taper(choix="remettre", reponses={
            "nom": "Luc Tremblay", "telephone": "514-555-0101", "employeur": "Électro Sud"})
        self.assertEqual(r["statut"], "ok", r.get("message"))
        pret = self.cadenas.loan_ids
        self.assertEqual((pret.borrower_name, pret.borrower_phone, pret.borrower_employer, pret.user_id),
                         ("Luc Tremblay", "514-555-0101", "Électro Sud", self.coordination))
        self.assertEqual(self.cadenas.holder_label, "Luc Tremblay · Électro Sud")

    def test_reprendre_demande_de_confirmer(self):
        self._taper(choix="remettre", reponses={"nom": "Luc", "telephone": "514-555-0101"})
        r = self._taper()
        self.assertEqual(r["statut"], "choice")
        self.assertFalse(self.cadenas.loan_ids.date_end, "Un simple tapotement ne reprend pas un cadenas.")
        r = self._taper(choix="reprendre")
        self.assertEqual(r["statut"], "ok", r.get("message"))
        self.assertTrue(self.cadenas.loan_ids.date_end)

    def test_le_registre_porte_les_colonnes_du_reglement(self):
        self._taper(choix="remettre", reponses={
            "nom": "Luc Tremblay", "telephone": "514-555-0101", "employeur": "Électro Sud"})
        self._taper(choix="reprendre")
        html, _format = self.env["ir.actions.report"]._render_qweb_html(
            "bf_nfc_loan.report_registre_cadenas", self.cadenas.ids)
        texte = html.decode()
        for attendu in ("Cadenas 7", "CAD-007", "Luc Tremblay", "514-555-0101", "Électro Sud",
                        self.coordination.name):
            self.assertIn(attendu, texte)
