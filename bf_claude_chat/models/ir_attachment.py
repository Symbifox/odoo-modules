from odoo import models

#: La clé de contexte sous laquelle `/claude-chat/upload` crée une pièce de Gen.
CLE_PIECE_GEN = "bf_gen_piece"


class IrAttachment(models.Model):
    _inherit = "ir.attachment"

    def _index(self, bin_data, file_type, checksum=None):
        """Une pièce de Gen n'est jamais indexée par Odoo.

        🔴 Son texte est déjà lu, dans un sous-processus borné (`pieces_jointes`). Sans
        cette garde, `attachment_indexation` (installé sur plusieurs locataires) relisait
        le fichier DANS le worker, sans plafond : pdfminer sur toutes les pages d'un PDF,
        `minidom` sur tout le XML d'un docx. Donner `application/octet-stream` ne suffisait
        pas : Odoo redevine le type depuis le contenu, et ce module essaie tous ses
        formats quel que soit le type. Le texte lu est écrit ensuite dans `index_content`.
        """
        if self.env.context.get(CLE_PIECE_GEN):
            return False
        return super()._index(bin_data, file_type, checksum=checksum)
