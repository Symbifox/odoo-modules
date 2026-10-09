from odoo.tests import BaseCase, tagged

from odoo.addons.bf_document_export.models.export_template import (
    clean_component,
    strip_code_prefix,
)


@tagged("post_install", "-at_install")
class TestNaming(BaseCase):
    def test_forbidden_characters(self):
        self.assertEqual(clean_component('a"b*c<d>e?f|g'), "a-b-c-d-e-f-g")
        self.assertEqual(clean_component("Contrat/Entente"), "Contrat-Entente")
        self.assertEqual(clean_component("POL-005 : Classement des fichiers"), "POL-005 - Classement des fichiers")

    def test_trailing_dots_spaces_and_controls(self):
        self.assertEqual(clean_component("Politique FR. "), "Politique FR")
        self.assertEqual(clean_component("a\tb\n  c"), "a b c")

    def test_reserved_names(self):
        self.assertEqual(clean_component("CON"), "_CON")
        self.assertEqual(clean_component("com1.txt"), "_com1.txt")
        self.assertEqual(clean_component("desktop.ini"), "_desktop.ini")
        self.assertEqual(clean_component("~$brouillon.docx"), "brouillon.docx")
        self.assertEqual(clean_component(""), "_")

    def test_code_prefix(self):
        self.assertEqual(strip_code_prefix("POL-004 — Sécurité", "POL-004"), "Sécurité")
        self.assertEqual(strip_code_prefix("POL-005 : Classement des fichiers", "POL-005"), "Classement des fichiers")
        self.assertEqual(strip_code_prefix("Procédure", "PRO-002"), "Procédure")
        self.assertEqual(strip_code_prefix("PRO-002", "PRO-002"), "PRO-002")
