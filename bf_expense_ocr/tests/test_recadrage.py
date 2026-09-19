# -*- coding: utf-8 -*-
"""La copie recadrée se pose À CÔTÉ de la photo, jamais à sa place.

Arbitrage du 2026-09-18 : on garde les deux. L'originale est la
pièce justificative au sens comptable et personne ne doit pouvoir dire qu'on
l'a modifiée ; la recadrée est une copie de travail, droite et lisible sans
zoomer.

🔴 Le contrôle qui compte vraiment est `test_une_seconde_lecture_ne_lit_pas_le
_recadrage` : sans exclusion explicite, `_ocr_attachment` prend la pièce la
plus RÉCENTE, et la plus récente est justement celle que la lecture précédente
a posée. On recadrerait un recadrage, en silence.
"""

from odoo.tests import tagged

from .common import PNG_1x1, BancLecture


@tagged("post_install", "-at_install", "bf_expense_ocr")
class TestRecadrage(BancLecture):

    #: Ce que le pont rend quand il a su ne garder que le papier.
    CROP = {
        "fait": True,
        "motif": "339x1538, 19% du cadre",
        "avant": [1440, 1920],
        "apres": [339, 1538],
        "part": 0.189,
        "ext": ".jpg",
        "mimetype": "image/jpeg",
    }

    def _enveloppe_recadree(self, donnees=None, crop=None):
        return {
            "data": donnees if donnees is not None else self.RECU_JUSTE,
            "error": None,
            "cropped_base64": PNG_1x1,
            "crop": crop if crop is not None else self.CROP,
        }

    def _recadrees(self, depense):
        return self.env["ir.attachment"].search([
            ("res_model", "=", "hr.expense"),
            ("res_id", "=", depense.id),
            ("description", "=", "bf_ocr_recadre"),
        ])

    def test_la_copie_est_posee_a_cote(self):
        depense = self._depense_vierge(nom_piece="IMG_4821.jpg",
                                       mimetype="image/jpeg")
        originale = depense.message_main_attachment_id
        with self._passerelle(enveloppe=self._enveloppe_recadree()):
            self.assertTrue(depense.action_ocr_scan())

        copies = self._recadrees(depense)
        self.assertEqual(len(copies), 1, "une copie recadrée, pas plus")
        self.assertIn("recadré", copies.name)
        self.assertEqual(copies.mimetype, "image/jpeg")
        self.assertTrue(copies.datas)

        self.assertTrue(originale.exists(), "l'originale ne doit pas disparaître")
        self.assertEqual(depense.message_main_attachment_id, originale,
                         "la pièce justificative reste la principale du fil")

    def test_une_seconde_lecture_ne_lit_pas_le_recadrage(self):
        """🔴 Le piège : la copie est la pièce la plus récente.

        On se place sans pièce principale, pour éprouver la RECHERCHE et pas le
        raccourci `message_main_attachment_id`.
        """
        depense = self._depense_vierge(avec_piece=False)
        originale = self.env["ir.attachment"].create({
            "name": "PXL_20251130.jpg",
            "res_model": "hr.expense",
            "res_id": depense.id,
            "datas": PNG_1x1,
            "mimetype": "image/jpeg",
        })
        self.assertEqual(depense._ocr_attachment(), originale)

        with self._passerelle(enveloppe=self._enveloppe_recadree()):
            depense.action_ocr_scan()
        copie = self._recadrees(depense)
        self.assertTrue(copie, "la copie doit bien avoir été posée")
        self.assertGreater(copie.id, originale.id, "elle est bien la plus récente")

        self.assertEqual(depense._ocr_attachment(), originale,
                         "la seconde lecture doit relire la PHOTO, pas la copie")

    def test_la_principale_recadree_est_ecartee_aussi(self):
        """Même si quelqu'un promeut la copie en pièce principale du fil."""
        depense = self._depense_vierge(avec_piece=False)
        originale = self.env["ir.attachment"].create({
            "name": "photo.jpg", "res_model": "hr.expense", "res_id": depense.id,
            "datas": PNG_1x1, "mimetype": "image/jpeg",
        })
        copie = self.env["ir.attachment"].create({
            "name": "photo (recadré).jpg", "res_model": "hr.expense",
            "res_id": depense.id, "datas": PNG_1x1, "mimetype": "image/jpeg",
            "description": "bf_ocr_recadre",
        })
        depense._message_set_main_attachment_id(copie, force=True)
        self.assertEqual(depense._ocr_attachment(), originale)

    def test_pas_de_copie_quand_le_pont_n_a_rien_recadre(self):
        """Un refus de recadrage n'est pas une panne, et ne pose rien."""
        enveloppe = {
            "data": self.RECU_JUSTE, "error": None, "cropped_base64": None,
            "crop": {"fait": False, "motif": "aire 88% hors bornes"},
        }
        depense = self._depense_vierge()
        with self._passerelle(enveloppe=enveloppe):
            self.assertTrue(depense.action_ocr_scan())
        self.assertEqual(depense.ocr_state, "done")
        self.assertFalse(self._recadrees(depense))

    def test_l_ancienne_enveloppe_sans_recadrage_passe_toujours(self):
        """⚠️ Le pont d'avant ne connaît pas ces champs. Rien ne doit casser."""
        depense = self._depense_vierge()
        with self._passerelle(self.RECU_JUSTE):
            self.assertTrue(depense.action_ocr_scan())
        self.assertFalse(self._recadrees(depense))

    def test_la_copie_est_posee_meme_quand_le_garde_fou_refuse(self):
        """C'est quand le modèle se trompe qu'on veut voir ce qu'il a lu."""
        faux = dict(self.RECU_JUSTE, total=41.30)
        depense = self._depense_vierge()
        with self._passerelle(enveloppe=self._enveloppe_recadree(faux)):
            self.assertFalse(depense.action_ocr_scan())
        self.assertEqual(depense.ocr_state, "doubt")
        self.assertTrue(self._recadrees(depense),
                        "la copie sert justement à comprendre le refus")

    def test_une_copie_impossible_ne_coute_pas_la_lecture(self):
        """Perdre la copie de travail ne doit pas perdre la lecture."""
        enveloppe = self._enveloppe_recadree(crop={"fait": True, "ext": ".jpg",
                                                   "mimetype": "image/jpeg"})
        enveloppe["cropped_base64"] = "ceci n'est pas du base64 !!"
        depense = self._depense_vierge()
        with self._passerelle(enveloppe=enveloppe):
            self.assertTrue(depense.action_ocr_scan(),
                            "la lecture doit aboutir malgré la copie ratée")
        self.assertEqual(depense.ocr_state, "done")
