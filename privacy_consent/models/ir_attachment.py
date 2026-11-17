"""Une pièce jointe sait s'anonymiser : son renseignement personnel EST son contenu.

Avant ce fichier, le cas de la pièce jointe vivait en dur dans
``destroy_record`` : c'était le seul modèle que « anonymisation » effaçait
vraiment, et ça ne se voyait pas depuis le modèle. Le crochet le rend lisible là
où on le cherche, et l'étiquette rendue au journal nomme la substitution.
"""
from odoo import models

from .privacy_destruction_ops import _clear_attachment_payload


class IrAttachment(models.Model):
    _inherit = "ir.attachment"

    def _privacy_anonymize(self):
        """Vide la charge utile ET le nom du fichier, puis les nomme.

        ⚠ Archiver une pièce jointe laisserait les octets dans le filestore,
        intacts et récupérables, pendant que le certificat annonce « détruit ».

        🔴 **Le nom du fichier EST un renseignement personnel**. La version d'avant ne touchait pas ``name``. Or ``_rec_name``
        vaut ``name`` : ``refresh_res_name`` relisait donc une valeur
        INCHANGÉE, la ligne de campagne gardait son ``res_name`` d'origine, et
        l'entrée immuable du registre portait « Document :
        consentement-nom-de-personne.pdf » avec « Éléments détruits : datas,
        description ». Le nom de la personne survivait à l'effacement de la
        pièce, sur le seul modèle que le module ait toujours prétendu effacer
        pour de vrai. Le socle se contredisait lui-même, l'anonymiseur de la
        preuve de consentement vidant bien ``attachment_filename``.

        ⚠ ``name`` est obligatoire sur ``ir.attachment`` : il est REMPLACÉ par
        un substitut non identifiant, comme le nom d'une fiche de contact, et
        non vidé. L'extension part avec le reste : « .pdf » ne dit rien d'utile
        sur une pièce dont la charge utile n'existe plus, et un nom conservé
        « pour la lisibilité » est exactement ce qui a fait sceller un nom de
        personne.
        """
        self.ensure_one()
        _clear_attachment_payload(self)
        champs = ["datas", "description"]
        substitut = "piece-jointe-anonymisee-%s" % self.id
        if self.name and self.name != substitut:
            self.sudo().write({"name": substitut})
            champs.append("name")
        return champs
