from odoo.tests.common import tagged

from .common import SocleVersion


@tagged("post_install", "-at_install")
class TestRetention(SocleVersion):

    def _vieillir(self, versions, jours):
        """Reculer `create_date` en SQL.

        Passer par l'ORM ne marche pas : `create_date` est un champ magique
        qu'Odoo repose lui-même à chaque écriture, donc le test passerait au
        vert en n'ayant rien vieilli du tout.
        """
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE bf_attachment_version SET create_date = now() - %s * interval '1 day' "
            "WHERE id IN %s",
            (jours, tuple(versions.ids)),
        )
        self.env.invalidate_all()

    def test_plafond_par_nombre_a_l_ecriture(self):
        self.regler("max_versions", "2")
        piece = self.creer_piece(octets=b"un")
        for octets in (b"deux", b"trois", b"quatre", b"cinq"):
            piece.write({"raw": octets})

        versions = self.versions_de(piece)
        self.assertEqual(len(versions), 2, "seules les plus récentes sont gardées")
        self.assertEqual(versions.mapped("numero"), [3, 4])
        self.assertEqual([v.content_id.raw for v in versions], [b"trois", b"quatre"])

    def test_plafond_a_zero_ne_purge_rien(self):
        self.regler("max_versions", "0")
        piece = self.creer_piece(octets=b"un")
        for octets in (b"deux", b"trois", b"quatre", b"cinq"):
            piece.write({"raw": octets})
        self.assertEqual(len(self.versions_de(piece)), 4)

    def test_purge_par_age(self):
        self.regler("max_versions", "0")
        self.regler("max_jours", "30")
        piece = self.creer_piece(octets=b"un")
        piece.write({"raw": b"deux"})
        piece.write({"raw": b"trois"})

        vieille = self.versions_de(piece)[0]
        self._vieillir(vieille, jours=90)

        self.Version.sudo()._cron_purger()

        restantes = self.versions_de(piece)
        self.assertEqual(restantes.mapped("numero"), [2],
                         "la version de 90 jours doit partir, celle du jour rester")

    def test_purge_par_age_desactivee(self):
        self.regler("max_versions", "0")
        self.regler("max_jours", "0")
        piece = self.creer_piece(octets=b"un")
        piece.write({"raw": b"deux"})
        self._vieillir(self.versions_de(piece), jours=900)
        self.Version.sudo()._cron_purger()
        self.assertEqual(len(self.versions_de(piece)), 1)

    def test_le_cron_rattrape_les_depassements_existants(self):
        """Le plafond a pu être abaissé après coup : le cron doit rattraper."""
        self.regler("max_versions", "0")
        piece = self.creer_piece(octets=b"un")
        for octets in (b"deux", b"trois", b"quatre", b"cinq"):
            piece.write({"raw": octets})
        self.assertEqual(len(self.versions_de(piece)), 4)

        self.regler("max_versions", "1")
        supprimees = self.Version.sudo()._cron_purger()

        self.assertEqual(supprimees, 3)
        self.assertEqual(self.versions_de(piece).mapped("numero"), [4])

    def test_la_purge_emporte_les_contenus(self):
        self.regler("max_versions", "1")
        piece = self.creer_piece(octets=b"un")
        piece.write({"raw": b"deux"})
        contenu_purge = self.versions_de(piece).content_id.id
        piece.write({"raw": b"trois"})

        self.assertFalse(
            self.Piece.sudo().with_context(skip_res_field_check=True)
            .browse(contenu_purge).exists(),
            "une purge qui laisse le contenu derrière elle ne libère rien")
