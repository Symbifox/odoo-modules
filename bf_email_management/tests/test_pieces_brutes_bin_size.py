"""L'encadré des pièces d'une ligne IMAP, lu comme le lit le navigateur.

Le client web lit la fiche avec ``bin_size=True``. ``raw_rfc822`` y vaut alors
sa taille (« 156.46 Kb »), pas le base64 : décodé tel quel, il levait,
l'encadré restait vide et le bouton « Extraire » disparaissait avec lui. Les
essais d'avant lisaient sans ce contexte, et passaient. Signalé le 2026-09-15
sur une carte d'embarquement restée invisible.
"""

from odoo.tests import TransactionCase

from .common import build_rfc822


class RawAttachmentBinSizeCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.row = cls.env["bf.email"].create({
            "subject": "Carte d'embarquement",
            "email_from": "no.reply@transporteur.test",
            "direction": "in",
            "status": "new",
            "source": "imap",
            "message_id_header": "<carte-embarquement@test.invalid>",
            "date": "2026-09-15 10:44:02",
            "raw_rfc822": build_rfc822(
                "Carte d'embarquement", "no.reply@transporteur.test",
                "owner@test.invalid", "Bon vol.",
                attachment="BoardingPass.csv"),
            "has_attachments": True,
            "attachment_count": 1,
        })

    def _vu_du_navigateur(self, fields):
        """``read`` sous ``bin_size=True``, le contexte que pose ``web_read``."""
        self.row.invalidate_recordset()
        return self.row.with_context(bin_size=True).read(fields)[0]

    def test_bin_size_rend_une_taille(self):
        """Le postulat de tout le correctif. S'il tombe, c'est Odoo qui a changé."""
        valeur = self._vu_du_navigateur(["raw_rfc822"])["raw_rfc822"]
        if isinstance(valeur, bytes):
            valeur = valeur.decode()
        self.assertRegex(valeur, r"^[\d.]+ (bytes|Kb|Mb|Gb)$")

    def test_le_navigateur_voit_l_encadre(self):
        vu = self._vu_du_navigateur(["raw_attachment_summary", "attachment_ids"])
        self.assertFalse(vu["attachment_ids"])
        self.assertTrue(
            vu["raw_attachment_summary"],
            "sous bin_size, l'encadré (et son bouton « Extraire ») disparaît",
        )
        self.assertIn("BoardingPass.csv", vu["raw_attachment_summary"])

    def test_extraire_puis_relire_comme_le_navigateur(self):
        self.row.action_extract_attachments()
        vu = self._vu_du_navigateur(["raw_attachment_summary", "attachment_ids"])
        noms = self.env["ir.attachment"].browse(vu["attachment_ids"]).mapped("name")
        self.assertEqual(noms, ["BoardingPass.csv"])
        self.assertFalse(vu["raw_attachment_summary"],
                         "une fois extraites, l'onglet montre les fichiers")
