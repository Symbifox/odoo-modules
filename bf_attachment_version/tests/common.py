from odoo.tests.common import TransactionCase


class SocleVersion(TransactionCase):
    """Outillage commun : une pièce jointe bureautique et de quoi la réécrire."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Piece = cls.env["ir.attachment"]
        cls.Version = cls.env["bf.attachment.version"]
        cls.Param = cls.env["ir.config_parameter"].sudo()

    def regler(self, cle, valeur):
        self.Param.set_param("bf_attachment_version." + cle, valeur)

    def creer_piece(self, nom="livrable.odt", octets=b"contenu initial", **extra):
        valeurs = {
            "name": nom,
            "raw": octets,
            "mimetype": "application/vnd.oasis.opendocument.text",
        }
        valeurs.update(extra)
        return self.Piece.create(valeurs)

    def versions_de(self, piece):
        return self.Version.sudo().search(
            [("attachment_id", "=", piece.id)], order="numero")
