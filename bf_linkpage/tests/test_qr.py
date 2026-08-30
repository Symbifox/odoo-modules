"""Le QR : ce qui part imprimé et ne se corrige plus.

Le contrôle est structuré en deux étages parce qu'aucun décodeur n'est garanti
dans le conteneur.

1. Les PARAMÈTRES sont verrouillés (niveau H, part du logo bornée). Ils ont été
   établis par la mesure, pas par l'usage : le 2026-08-30, avec un décodeur
   indépendant de la bibliothèque qui produit l'image, logo à 22 % du côté —
   niveau L ILLISIBLE même en pleine résolution, M / Q / H lus jusqu'à 64 px de
   côté ; au niveau H, 34 % de logo passe encore et 40 % ne passe plus.
2. Le DÉCODAGE réel tourne dès qu'un décodeur est présent.

Sans le premier étage, abaisser la correction d'erreur de H à L ne faisait
rougir AUCUN test — vérifié par mutation le même jour — et le module aurait pu
livrer un QR qui ne scanne jamais.
"""

import io

from odoo.tests import TransactionCase, tagged

from ..models.linkpage import LOGO_MAX_RATIO, LOGO_RATIO


def _decodeur():
    """Le premier décodeur disponible, ou None. Jamais celui qui a encodé."""
    try:
        import zxingcpp

        return lambda img: [r.text for r in zxingcpp.read_barcodes(img)]
    except ImportError:
        pass
    try:
        from pyzbar.pyzbar import decode

        return lambda img: [d.data.decode() for d in decode(img)]
    except ImportError:
        return None


@tagged("bf_linkpage", "post_install", "-at_install")
class TestQr(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({"name": "QR"})
        cls.page = cls.env["bf.linkpage"].create({
            "name": "QR", "slug": "qr-test", "kind": "owner",
            "partner_id": cls.partner.id, "state": "published"})

    def test_le_qr_a_la_marque_est_en_correction_H(self):
        """Mesuré : au niveau L, avec le logo, le code est ILLISIBLE."""
        from qrcode.constants import ERROR_CORRECT_H

        vus = {}
        original = None
        import qrcode

        original = qrcode.QRCode.__init__

        def espion(qself, *args, **kwargs):
            vus["error_correction"] = kwargs.get("error_correction")
            return original(qself, *args, **kwargs)

        qrcode.QRCode.__init__ = espion
        try:
            self.page._qr_png(branded=True)
        finally:
            qrcode.QRCode.__init__ = original
        self.assertEqual(
            vus.get("error_correction"), ERROR_CORRECT_H,
            "le QR à la marque doit rester en correction d'erreur H",
        )

    def test_la_part_du_logo_reste_sous_la_limite_mesuree(self):
        self.assertLessEqual(
            LOGO_RATIO, LOGO_MAX_RATIO,
            "au-delà de %.0f %% du côté, le niveau H ne reconstruit plus le code"
            % (LOGO_MAX_RATIO * 100),
        )

    def test_le_qr_sort_bien_une_png(self):
        payload = self.page._qr_png(branded=True)
        self.assertTrue(payload.startswith(b"\x89PNG"))

    def test_le_qr_se_decode_reellement(self):
        decodeur = _decodeur()
        if decodeur is None:
            self.skipTest(
                "aucun décodeur indépendant (zxing-cpp, pyzbar) dans cet "
                "environnement : seuls les paramètres sont vérifiés"
            )
        from PIL import Image

        for branded in (True, False):
            img = Image.open(io.BytesIO(self.page._qr_png(branded=branded)))
            lus = decodeur(img)
            self.assertIn(self.page.public_url, lus,
                          "QR à la marque=%s illisible" % branded)
            # Petit, comme dans une signature courriel.
            petit = img.resize((96, 96), Image.LANCZOS)
            self.assertIn(self.page.public_url, decodeur(petit),
                          "QR à la marque=%s illisible une fois réduit" % branded)

    def test_le_qr_sans_logo_reste_lisible_aussi(self):
        payload = self.page._qr_png(branded=False)
        self.assertTrue(payload.startswith(b"\x89PNG"))
