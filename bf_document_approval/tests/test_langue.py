# -*- coding: utf-8 -*-
"""La langue du refus qui bloque une publication.

La source est en anglais et le français vient de i18n/fr_CA.po. Le refus nomme le motif de chaque
avis contraire, et « sans motif » quand il n'y en a pas : ce mot était logé dans les arguments
d'un autre `_()`, que l'extracteur ne voyait pas, et dans une expression génératrice, où `_()`
n'a pas de `self` pour lire la langue.
"""
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, new_test_user, tagged

USAGER = "base.group_user,project_knowledge_matrix.group_document_user"


@tagged("post_install", "-at_install")
class TestLangue(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for code in ("en_US", "fr_CA"):
            cls.env["res.lang"]._activate_lang(code)
        cls.tremblay = new_test_user(
            cls.env, "tremblay.langue25680", groups=USAGER, name="M. Tremblay")
        projet = cls.env["project.project"].create({"name": "Essai de langue"})
        projet.message_subscribe(partner_ids=cls.tremblay.partner_id.ids)
        matrice = cls.env["project.knowledge.matrix"].create({
            "name": "Matrice de langue", "project_id": projet.id})
        type_doc = cls.env["project.document.type"].create({
            "name": "Politique de langue", "code": "PLNG25680"})
        document = cls.env["project.document"].create({
            "name": "Politique de langue", "code": "POL-LNG25680",
            "matrix_id": matrice.id, "type_id": type_doc.id, "project_id": projet.id})
        cls.version = cls.env["project.document.version"].create({
            "document_id": document.id, "version_number": "1.0"})

    def test_un_refus_sans_motif_se_dit_dans_la_langue_de_qui_publie(self):
        ligne = self.env["project.document.approver"].create({
            "version_id": self.version.id, "user_id": self.tremblay.id, "requis": True})
        ligne.with_user(self.tremblay).write({"commentaire": "Non."})
        ligne.with_user(self.tremblay).action_refuser()
        # Le motif effacé après coup (par un flux serveur) : le refus doit encore le dire.
        ligne.sudo().write({"commentaire": False})
        for lang, texte in (("fr_CA", "sans motif"), ("en_US", "no reason given")):
            with self.assertRaises(UserError) as refus:
                self.version.with_context(lang=lang).action_release()
            self.assertIn(texte, str(refus.exception))
