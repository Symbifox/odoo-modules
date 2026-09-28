from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged

from .commun import monter


@tagged("post_install", "-at_install")
class TestAssociation(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        monter(cls)

    def _associer(self, user, tag, geste, **kw):
        return tag.with_user(user)._associer(geste, **kw)

    def test_l_association_survit_a_la_fin_de_transaction(self):
        # Le suivi de l'historique ne tourne qu'au vidage final : sans ce flush,
        # une association qui plante au premier vrai clic passe au vert ici.
        tag = self.etiquettes[0]
        self._associer(self.gestion, tag, self.geste_open, cible=self.partenaire)
        self.env.cr.flush()
        autre = self.env["res.partner"].create({"name": "Autre salle (essai)"})
        tag.with_user(self.gestion).write({"res_id": autre.id})
        self.env.cr.flush()
        tag.with_user(self.gestion)._reinitialiser()
        self.env.cr.flush()
        self.assertTrue(tag.qr_vierge)

    def test_la_gestion_associe(self):
        tag = self.etiquettes[0]
        self._associer(self.gestion, tag, self.geste_open, cible=self.partenaire)
        self.assertFalse(tag.qr_vierge)
        self.assertEqual((tag.res_model, tag.res_id), ("res.partner", self.partenaire.id))
        self.assertEqual(tag.name, self.partenaire.display_name)

    def test_le_groupe_choisi_par_la_societe_associe(self):
        tag = self.etiquettes[1]
        self._associer(self.concierge, tag.with_user(self.concierge),
                       self.geste_url, url="https://symbifox.com", public=True)
        self.assertEqual(tag._params()["url"], "https://symbifox.com")
        self.assertTrue(tag.qr_public)

    def test_un_interne_ordinaire_ne_peut_pas(self):
        with self.assertRaises(AccessError):
            self._associer(self.interne, self.etiquettes[2], self.geste_open, cible=self.partenaire)

    def test_retirer_le_groupe_retire_le_droit(self):
        self.societe.bf_qr_groupe_ids = [(5, 0, 0)]
        with self.assertRaises(AccessError):
            self._associer(self.concierge, self.etiquettes[2], self.geste_open, cible=self.partenaire)

    def test_geste_reserve_refuse_hors_gestion(self):
        cron = self.env["ir.cron"].search([], limit=1)
        with self.assertRaises(AccessError):
            self._associer(self.concierge, self.etiquettes[3], self.env.ref("bf_nfc.gesture_cron"),
                           cible=cron)

    def test_adresse_non_web_refusee(self):
        # « javascript://hôte/ » a un hôte : seul le contrôle du schéma l'arrête.
        for adresse in ("javascript:alert(1)", "javascript://symbifox.com/%0Aalert(1)",
                        "ftp://symbifox.com/x", "file:///etc/passwd", "symbifox.com", ""):
            with self.assertRaises(UserError):
                self._associer(self.gestion, self.etiquettes[4], self.geste_url, url=adresse)

    def test_type_de_fiche_hors_liste_refuse(self):
        with self.assertRaises(UserError):
            self._associer(self.gestion, self.etiquettes[4], self.geste_open,
                           cible=self.env["res.currency"].search([], limit=1))

    def test_geste_qui_ecrit_jamais_public(self):
        tag = self.etiquettes[5]
        self._associer(self.gestion, tag, self.geste_note, cible=self.partenaire, public=True)
        self.assertFalse(tag.qr_public)

    def test_deja_associee_refusee(self):
        tag = self.etiquettes[6]
        self._associer(self.gestion, tag, self.geste_open, cible=self.partenaire)
        with self.assertRaises(UserError):
            self._associer(self.gestion, tag, self.geste_url, url="https://symbifox.com")

    def test_une_pastille_hors_lot_ne_passe_pas_par_ici(self):
        pastille = self.env["bf.nfc.tag"].create({"name": "NFC", "gesture_id": self.geste_open.id,
                                                  "res_model": "res.partner",
                                                  "res_id": self.partenaire.id})
        with self.assertRaises(UserError):
            pastille.with_user(self.gestion)._reinitialiser()

    def test_reinitialiser_garde_le_code_le_numero_et_le_journal(self):
        tag = self.etiquettes[7]
        code = tag.code
        self._associer(self.gestion, tag, self.geste_open, cible=self.partenaire, place="Porte")
        tag._journaliser("session", "ok", "essai")
        tag.with_user(self.concierge).action_reinitialiser_qr()
        self.assertTrue(tag.qr_vierge)
        self.assertFalse(tag.res_model or tag.res_id or tag.place or tag.params)
        self.assertEqual((tag.code, tag.qr_reference, tag.name), (code, "TST-0008", "TST-0008"))
        self.assertEqual(len(tag.tap_ids), 1)
        self.assertIn("réinitialisée", tag.message_ids[0].body)

    def test_reinitialiser_en_lot_une_seule_fois(self):
        tags = self.etiquettes[8:10]
        for tag in tags:
            self._associer(self.gestion, tag, self.geste_open, cible=self.partenaire)
        action = tags.with_user(self.gestion).action_reinitialiser_qr_lot()
        assistant = self.env[action["res_model"]].with_user(self.gestion).create(
            {"tag_ids": [(6, 0, tags.ids)]})
        self.assertEqual(assistant.count_a_vider, 2)
        assistant.action_reinitialiser()
        self.assertTrue(all(tags.mapped("qr_vierge")))

    def test_reinitialiser_refuse_a_l_interne(self):
        tag = self.etiquettes[0]
        self._associer(self.gestion, tag, self.geste_open, cible=self.partenaire)
        with self.assertRaises(AccessError):
            tag.with_user(self.interne).action_reinitialiser_qr()

    def test_la_fiche_non_lisible_ne_s_associe_pas(self):
        # Un contact d'une société à laquelle le concierge n'appartient pas.
        autre = self.env["res.company"].create({"name": "Autre société (essai)"})
        partenaire = self.env["res.partner"].create({"name": "Caché", "company_id": autre.id})
        with self.assertRaises(AccessError):
            self._associer(self.concierge, self.etiquettes[0], self.geste_open, cible=partenaire)


@tagged("post_install", "-at_install")
class TestCloisonSocietes(TransactionCase):
    """Un gestionnaire d'une société ne touche pas aux étiquettes d'une autre."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        monter(cls)
        from odoo.tests import new_test_user
        cls.autre_societe = cls.env["res.company"].create({"name": "Société B (essai QR)"})
        cls.gestion_b = new_test_user(
            cls.env, login="qr_gestion_b", groups="base.group_user,bf_nfc.group_nfc_manager",
            company_id=cls.autre_societe.id, company_ids=[(6, 0, cls.autre_societe.ids)])
        cls.etiquettes[0].with_user(cls.gestion)._associer(cls.geste_open, cible=cls.partenaire)

    def test_reinitialiser_l_etiquette_d_une_autre_societe_refuse(self):
        with self.assertRaises(AccessError):
            self.etiquettes[0].with_user(self.gestion_b).action_reinitialiser_qr()
        self.assertFalse(self.etiquettes[0].qr_vierge)

    def test_associer_l_etiquette_d_une_autre_societe_refuse(self):
        with self.assertRaises(AccessError):
            self.etiquettes[1].with_user(self.gestion_b)._associer(
                self.geste_url, url="https://symbifox.com")
        self.assertTrue(self.etiquettes[1].qr_vierge)

    def test_imprimer_l_etiquette_d_une_autre_societe_refuse(self):
        assistant = self.env["bf.qr.imprimer"].with_user(self.gestion_b).create(
            {"tag_ids": [(6, 0, self.etiquettes[:2].ids)]})
        with self.assertRaises(UserError):
            assistant._pdf()
