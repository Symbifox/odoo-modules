"""Ce que le manifeste doit garantir pour une installation sur base NEUVE.

Le banc de référence est une copie de la production : un identifiant de menu y existe déjà, et une
vue chargée avant son menu parent y passait. Sur une base vide, le chargement s'arrête. Cet essai
lit le manifeste et ses fichiers, sans base de données particulière.
"""
import re

from odoo.modules import module as odoo_module
from odoo.tests import TransactionCase, tagged
from odoo.tools import file_open


@tagged("post_install", "-at_install")
class TestManifest(TransactionCase):

    def _xml_files(self):
        manifest = odoo_module.get_manifest("bf_gamification")
        return [name for name in manifest["data"] if name.endswith(".xml")]

    def test_a_menu_parent_is_loaded_before_its_children(self):
        defined, problems = {}, []
        for index, name in enumerate(self._xml_files()):
            with file_open("bf_gamification/%s" % name) as handle:
                source = handle.read()
            for match in re.finditer(r'<(?:menuitem|record)\b[^>]*\bid="([^"]+)"', source):
                defined.setdefault(match.group(1), (index, match.start()))
            for match in re.finditer(r'<menuitem\b[^>]*\bparent="([^".]+)"', source):
                parent = match.group(1)
                if parent not in defined or defined[parent] > (index, match.start()):
                    problems.append("%s : parent « %s » défini après, ou absent" % (name, parent))
        self.assertFalse(problems, "\n".join(problems))

    def test_every_data_file_exists(self):
        manifest = odoo_module.get_manifest("bf_gamification")
        for name in manifest["data"]:
            with file_open("bf_gamification/%s" % name) as handle:
                self.assertTrue(handle.read(), name)
