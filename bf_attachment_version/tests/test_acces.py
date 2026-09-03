from odoo.tests.common import new_test_user, tagged

from .common import SocleVersion


@tagged("post_install", "-at_install")
class TestAcces(SocleVersion):
    """Une version ne doit jamais rendre lisible ce que la pièce parente cache.

    Le discriminant utilisé ici ne demande aucun module supplémentaire :
    `ir.attachment._search` n'accorde une pièce sans `res_id` qu'à son créateur
    (ou à un administrateur). Deux usagers internes ordinaires suffisent donc à
    prouver que la règle mord.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.alice = new_test_user(cls.env, login="av_alice", groups="base.group_user")
        cls.bruno = new_test_user(cls.env, login="av_bruno", groups="base.group_user")

    def _piece_privee_d_alice(self):
        piece = self.Piece.with_user(self.alice).create({
            "name": "note privee.odt",
            "raw": b"le secret d'alice",
            "mimetype": "application/vnd.oasis.opendocument.text",
        })
        piece.write({"raw": b"le secret revise"})
        return piece

    def test_alice_voit_sa_version(self):
        piece = self._piece_privee_d_alice()
        self.env.invalidate_all()
        vues = self.env["bf.attachment.version"].with_user(self.alice).search(
            [("attachment_id", "=", piece.id)])
        self.assertEqual(len(vues), 1)

    def test_bruno_ne_voit_pas_la_version_d_alice(self):
        piece = self._piece_privee_d_alice()
        # Aucune lecture préalable du champ mesuré : le cache ORM appartient à la
        # transaction, pas à l'environnement, et une lecture antérieure rendrait
        # la mesure fausse.
        self.env.invalidate_all()
        vues = self.env["bf.attachment.version"].with_user(self.bruno).search(
            [("attachment_id", "=", piece.id)])
        self.assertFalse(vues, "la version d'une pièce illisible ne doit pas apparaître")

    def test_bruno_ne_voit_pas_non_plus_le_contenu_conserve(self):
        """Le vrai risque : contourner la règle par la pièce de conservation."""
        piece = self._piece_privee_d_alice()
        conservation = self.versions_de(piece).content_id
        self.env.invalidate_all()
        trouvee = self.env["ir.attachment"].with_user(self.bruno).search(
            [("id", "=", conservation.id)])
        self.assertFalse(trouvee,
                         "le contenu conservé doit suivre l'accès de la pièce parente")

    def test_alice_atteint_le_contenu_conserve(self):
        """Le contrôle doit discriminer, pas tout refuser."""
        piece = self._piece_privee_d_alice()
        conservation = self.versions_de(piece).content_id
        self.env.invalidate_all()
        trouvee = self.env["ir.attachment"].with_user(self.alice).search(
            [("id", "=", conservation.id)])
        self.assertEqual(len(trouvee), 1)

    def test_le_superusager_voit_tout(self):
        piece = self._piece_privee_d_alice()
        self.env.invalidate_all()
        self.assertEqual(
            len(self.env["bf.attachment.version"].sudo().search(
                [("attachment_id", "=", piece.id)])), 1)

    def test_bruno_ne_peut_pas_restaurer(self):
        piece = self._piece_privee_d_alice()
        version = self.versions_de(piece)
        self.env.invalidate_all()
        with self.assertRaises(Exception):
            version.with_user(self.bruno).action_restaurer()

    # ------------------------------------------------------------------
    # Les chemins qui ne passent pas par une recherche
    # ------------------------------------------------------------------
    def test_bruno_ne_peut_pas_lire_une_version_par_son_identifiant(self):
        """Deviner un identifiant ne doit pas rendre le nom du fichier.

        Tenu par `_search` : cet essai tombe quand on retire le filtre, pas
        quand on retire la garde de `_check_access`.
        """
        from odoo.exceptions import AccessError
        piece = self._piece_privee_d_alice()
        version = self.versions_de(piece)
        self.env.invalidate_all()
        with self.assertRaises(AccessError):
            self.env["bf.attachment.version"].with_user(self.bruno).browse(
                version.id).read(["name", "file_size", "checksum"])

    def test_alice_lit_la_sienne_par_son_identifiant(self):
        """Le contrôle doit discriminer, pas tout refuser."""
        piece = self._piece_privee_d_alice()
        version = self.versions_de(piece)
        self.env.invalidate_all()
        lu = self.env["bf.attachment.version"].with_user(self.alice).browse(
            version.id).read(["name"])
        self.assertEqual(lu[0]["name"], "note privee.odt")

    def test_bruno_ne_peut_pas_telecharger_par_identifiant(self):
        from odoo.exceptions import AccessError
        piece = self._piece_privee_d_alice()
        version = self.versions_de(piece)
        self.env.invalidate_all()
        with self.assertRaises(AccessError):
            self.env["bf.attachment.version"].with_user(self.bruno).browse(
                version.id).action_telecharger()

    def test_has_access_dit_la_verite(self):
        piece = self._piece_privee_d_alice()
        version = self.versions_de(piece)
        self.env.invalidate_all()
        V = self.env["bf.attachment.version"]
        self.assertTrue(V.with_user(self.alice).browse(version.id).has_access("read"))
        self.assertFalse(V.with_user(self.bruno).browse(version.id).has_access("read"))
