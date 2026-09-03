from odoo.tests.common import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestDroitsEcriture(TransactionCase):
    """`pieces_modifiables` doit trier, pas tout accorder ni tout refuser."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.alice = new_test_user(cls.env, login="cool_alice", groups="base.group_user")
        cls.bruno = new_test_user(cls.env, login="cool_bruno", groups="base.group_user")
        cls.Aide = cls.env["bf.collabora.helper"]

    def _piece_d_alice(self):
        return self.env["ir.attachment"].with_user(self.alice).create({
            "name": "note.odt",
            "raw": b"contenu",
            "mimetype": "application/vnd.oasis.opendocument.text",
        })

    def test_alice_peut_ecrire_la_sienne(self):
        piece = self._piece_d_alice()
        self.env.invalidate_all()
        self.assertEqual(
            self.Aide.with_user(self.alice).pieces_modifiables([piece.id]), [piece.id])

    def test_bruno_ne_peut_pas(self):
        piece = self._piece_d_alice()
        self.env.invalidate_all()
        self.assertEqual(self.Aide.with_user(self.bruno).pieces_modifiables([piece.id]), [])

    def test_liste_vide(self):
        self.assertEqual(self.Aide.pieces_modifiables([]), [])

    def test_identifiant_inexistant_ne_leve_pas(self):
        """`check_access` de l'amont lève ; c'est ce qu'on remplace."""
        self.assertEqual(self.Aide.pieces_modifiables([999999999]), [])

    def test_melange(self):
        mienne = self.env["ir.attachment"].with_user(self.bruno).create({
            "name": "a_bruno.odt", "raw": b"x",
            "mimetype": "application/vnd.oasis.opendocument.text"})
        celle_d_alice = self._piece_d_alice()
        self.env.invalidate_all()
        resultat = self.Aide.with_user(self.bruno).pieces_modifiables(
            [mienne.id, celle_d_alice.id])
        self.assertEqual(resultat, [mienne.id],
                         "le tri doit garder la sienne et écarter l'autre")

    def test_piece_liee_a_un_enregistrement_partage(self):
        """Le contrôle doit aussi reporter sur l'enregistrement lié.

        Une pièce rattachée à un partenaire, que les deux peuvent écrire, doit
        être offerte aux deux : le correctif ne doit pas se contenter de
        refuser tout ce qui n'appartient pas à l'appelant.
        """
        # ⚠️ Écrire un `res.partner` demande `base.group_partner_manager` en
        # plus de `base.group_user`, sinon la création de la pièce elle-même
        # échoue et le test mesure la mauvaise chose.
        gestion = self.env.ref("base.group_partner_manager")
        (self.alice | self.bruno).write({"groups_id": [(4, gestion.id)]})
        partenaire = self.env["res.partner"].create({"name": "Client de banc"})
        piece = self.env["ir.attachment"].with_user(self.alice).create({
            "name": "partage.odt", "raw": b"x",
            "mimetype": "application/vnd.oasis.opendocument.text",
            "res_model": "res.partner", "res_id": partenaire.id,
        })
        self.env.invalidate_all()
        self.assertEqual(
            self.Aide.with_user(self.bruno).pieces_modifiables([piece.id]), [piece.id])

    def test_vider_le_cache_est_reserve_a_l_administration(self):
        from odoo.exceptions import AccessError
        with self.assertRaises(AccessError):
            self.Aide.with_user(self.alice).vider_cache_decouverte()

    def test_l_administration_peut_vider_le_cache(self):
        """Le garde doit discriminer, pas tout refuser."""
        from odoo.tests.common import new_test_user
        admin = new_test_user(self.env, login="cool_admin",
                              groups="base.group_user,base.group_system")
        self.assertIsInstance(
            self.Aide.with_user(admin).vider_cache_decouverte(), int)
