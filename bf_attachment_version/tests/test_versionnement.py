from odoo.tests.common import tagged

from .common import SocleVersion


@tagged("post_install", "-at_install")
class TestVersionnement(SocleVersion):

    def test_ecriture_conserve_le_contenu_precedent(self):
        piece = self.creer_piece(octets=b"version A")
        piece.write({"raw": b"version B"})

        versions = self.versions_de(piece)
        self.assertEqual(len(versions), 1, "un remplacement doit laisser une version")
        self.assertEqual(versions.numero, 1)
        self.assertEqual(versions.content_id.raw, b"version A",
                         "la version doit porter les octets d'AVANT, pas ceux d'après")
        self.assertEqual(piece.raw, b"version B")

    def test_contenu_identique_ne_cree_rien(self):
        """`force_storage` réécrit chaque pièce avec son propre contenu.

        Sans ce contrôle, une migration de magasin fabriquerait une version par
        pièce du parc, toutes identiques.
        """
        piece = self.creer_piece(octets=b"inchange")
        piece.write({"raw": b"inchange"})
        self.assertFalse(self.versions_de(piece))

    def test_ecriture_de_metadonnees_ne_cree_rien(self):
        piece = self.creer_piece()
        piece.write({"name": "livrable-renomme.odt"})
        piece.write({"description": "une note"})
        self.assertFalse(self.versions_de(piece))

    def test_versions_successives_numerotees_et_fideles(self):
        piece = self.creer_piece(octets=b"un")
        piece.write({"raw": b"deux"})
        piece.write({"raw": b"trois"})
        piece.write({"raw": b"quatre"})

        versions = self.versions_de(piece)
        self.assertEqual(versions.mapped("numero"), [1, 2, 3])
        self.assertEqual(
            [v.content_id.raw for v in versions],
            [b"un", b"deux", b"trois"],
            "chaque version porte l'état qu'elle a remplacé, dans l'ordre")

    def test_datas_en_base64_est_reconnu(self):
        import base64
        piece = self.creer_piece(octets=b"avant")
        piece.write({"datas": base64.b64encode(b"apres")})
        versions = self.versions_de(piece)
        self.assertEqual(len(versions), 1)
        self.assertEqual(versions.content_id.raw, b"avant")

    def test_datas_identique_ne_cree_rien(self):
        import base64
        piece = self.creer_piece(octets=b"pareil")
        piece.write({"datas": base64.b64encode(b"pareil")})
        self.assertFalse(self.versions_de(piece))

    # ------------------------------------------------------------------
    # Ce qui ne doit PAS être versionné
    # ------------------------------------------------------------------
    def test_champ_binaire_exclu(self):
        """Une pièce portant `res_field` est le stockage d'un champ, pas un document."""
        piece = self.creer_piece(
            nom="icone.odt", res_model="res.partner",
            res_id=self.env.user.partner_id.id, res_field="image_1920")
        piece.write({"raw": b"nouvelle image"})
        self.assertFalse(self.versions_de(piece))

    def test_modele_exclu(self):
        piece = self.creer_piece(nom="bundle.odt", res_model="ir.ui.view", res_id=1)
        piece.write({"raw": b"nouveau paquet"})
        self.assertFalse(self.versions_de(piece))

    def test_extension_hors_liste(self):
        piece = self.creer_piece(nom="capture.png", mimetype="image/png")
        piece.write({"raw": b"autres pixels"})
        self.assertFalse(self.versions_de(piece))

    def test_piece_sans_extension(self):
        piece = self.creer_piece(nom="sans_extension")
        piece.write({"raw": b"autre chose"})
        self.assertFalse(self.versions_de(piece))

    def test_au_dela_de_la_taille_maximale(self):
        self.regler("taille_max_mo", "1")
        gros = b"x" * (2 * 1024 * 1024)
        piece = self.creer_piece(octets=gros)
        piece.write({"raw": b"petit"})
        self.assertFalse(self.versions_de(piece))

    def test_module_eteint(self):
        self.regler("actif", "False")
        piece = self.creer_piece(octets=b"avant")
        piece.write({"raw": b"apres"})
        self.assertFalse(self.versions_de(piece))
        # et rallumé, le même geste conserve : le contrôle discrimine bien
        self.regler("actif", "True")
        piece.write({"raw": b"encore apres"})
        self.assertEqual(len(self.versions_de(piece)), 1)

    def test_contexte_de_court_circuit(self):
        piece = self.creer_piece(octets=b"avant")
        piece.with_context(bf_sans_version=True).write({"raw": b"apres"})
        self.assertFalse(self.versions_de(piece))

    def test_la_piece_de_conservation_ne_se_versionne_pas(self):
        """Sinon le contrôle d'accès des versions tournerait en rond."""
        piece = self.creer_piece(octets=b"avant")
        piece.write({"raw": b"apres"})
        conservation = self.versions_de(piece).content_id
        conservation.sudo().write({"raw": b"retouche"})
        self.assertFalse(
            self.Version.sudo().search([("attachment_id", "=", conservation.id)]))

    def test_creation_ne_cree_aucune_version(self):
        """Créer une pièce ne remplace rien : il n'y a pas d'état antérieur."""
        piece = self.creer_piece(octets=b"tout neuf")
        self.assertFalse(self.versions_de(piece))

    def test_ecriture_en_lot(self):
        """Le crochet doit tenir sur un ensemble, pas seulement sur un enregistrement."""
        a = self.creer_piece(nom="a.odt", octets=b"a1")
        b = self.creer_piece(nom="b.odt", octets=b"b1")
        (a | b).write({"raw": b"commun"})
        self.assertEqual(self.versions_de(a).content_id.raw, b"a1")
        self.assertEqual(self.versions_de(b).content_id.raw, b"b1")

    def test_copie_de_piece_ne_copie_pas_les_versions(self):
        """Un `copy` qui emporterait l'historique le dupliquerait sans le lier."""
        piece = self.creer_piece(octets=b"avant")
        piece.write({"raw": b"apres"})
        copie = piece.copy()
        self.assertEqual(len(self.versions_de(piece)), 1)
        self.assertFalse(self.versions_de(copie),
                         "une copie repart sans historique")

    def test_fichier_absent_du_magasin_ne_fabrique_pas_une_version_vide(self):
        """Un `store_fname` cassé rend b'' : mieux vaut rien conserver qu'un leurre."""
        piece = self.creer_piece(octets=b"avant")
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE ir_attachment SET store_fname = %s WHERE id = %s",
            ("ff/ffffffffffffffffffffffffffffffffffffffff", piece.id))
        self.env.invalidate_all()
        piece.write({"raw": b"apres"})
        self.assertFalse(self.versions_de(piece))

    # ------------------------------------------------------------------
    # Restauration
    # ------------------------------------------------------------------
    def test_restauration_remet_le_contenu_et_conserve_ce_qu_elle_remplace(self):
        piece = self.creer_piece(octets=b"originale")
        piece.write({"raw": b"abimee"})
        premiere = self.versions_de(piece)

        premiere.action_restaurer()

        self.assertEqual(piece.raw, b"originale")
        versions = self.versions_de(piece)
        self.assertEqual(versions.mapped("numero"), [1, 2])
        self.assertEqual(versions[1].content_id.raw, b"abimee",
                         "revenir en arrière ne doit rien perdre non plus")

    # ------------------------------------------------------------------
    # Suppression
    # ------------------------------------------------------------------
    def test_suppression_de_la_piece_emporte_versions_et_contenus(self):
        piece = self.creer_piece(octets=b"avant")
        piece.write({"raw": b"apres"})
        version = self.versions_de(piece)
        contenu_id = version.content_id.id
        version_id = version.id

        piece.unlink()

        self.assertFalse(self.Version.sudo().browse(version_id).exists())
        self.assertFalse(
            self.Piece.sudo().with_context(skip_res_field_check=True)
            .browse(contenu_id).exists(),
            "la cascade SQL ne doit pas laisser la pièce de conservation orpheline")

    # ------------------------------------------------------------------
    # 🔴 Le piège : l'ancien fichier survit-il vraiment au ramasse-miettes ?
    # ------------------------------------------------------------------
    def test_le_fichier_conserve_survit_au_ramasse_miettes(self):
        """La pièce de conservation partage le fichier de l'original.

        Écrire la version ne recopie aucun octet : le magasin range par sha1 et
        retrouve le fichier déjà là. Ce qui pourrait mal tourner, c'est le
        ramasse-miettes du magasin, qui passe juste après l'écrasement et voit
        l'ancien chemin dans sa liste. S'il ne comptait pas les références, la
        version pointerait vers un fichier disparu et le module mentirait en
        silence.
        """
        if self.Piece._storage() != "file":
            self.skipTest("magasin en base : pas de fichier à ramasser")

        piece = self.creer_piece(octets=b"les octets qui doivent survivre")
        piece.write({"raw": b"les octets qui remplacent"})
        version = self.versions_de(piece)
        self.env.flush_all()

        self.Piece.sudo()._gc_file_store_unsafe()

        self.env.invalidate_all()
        self.assertEqual(
            version.content_id.sudo().raw, b"les octets qui doivent survivre",
            "le ramasse-miettes a emporté le contenu conservé")


@tagged("post_install", "-at_install")
class TestOrigine(SocleVersion):
    """L'origine est déduite du chemin HTTP.

    Aucun des deux connecteurs bureautiques n'est un module maison : on ne peut
    pas leur demander de poser un indicateur. Ce test fige la correspondance,
    parce qu'un renommage de route côté connecteur la casserait en silence et
    que toutes les versions retomberaient sur « autre ».
    """

    def _origine_pour(self, chemin):
        from unittest.mock import patch as remplacer

        class RequeteFactice:
            class httprequest:
                path = chemin

        with remplacer("odoo.http.request", RequeteFactice):
            return self.env["ir.attachment"]._bf_origine()

    def test_chemins_reconnus(self):
        self.assertEqual(self._origine_pour("/onlyoffice/editor/callback/12"), "onlyoffice")
        self.assertEqual(self._origine_pour("/collabora_odoo/wopi/files/12/contents"), "collabora")
        self.assertEqual(self._origine_pour("/web/dataset/call_kw"), "interface")
        self.assertEqual(self._origine_pour("/mail/attachment/upload"), "interface")
        self.assertEqual(self._origine_pour("/quelque/autre/chose"), "autre")

    def test_hors_requete(self):
        """Un cron ou un script n'a pas de requête : ça ne doit pas lever."""
        self.assertEqual(self.env["ir.attachment"]._bf_origine(), "autre")
