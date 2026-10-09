"""Un courriel classé en note garde sa bulle verte ou bleue.

La couleur se décide au navigateur (`Message.bubbleColor`) : aucun test Python
ne la voit, un parcours au navigateur l'a lue dans le DOM. Ce qui se
garde ici, c'est que le correctif est BIEN dans le paquet servi, et que le
classement reste une note : le dessin ne doit jamais devenir une raison de
repasser en « Discussion ».
"""
from odoo.tests import tagged

from .common import MobileApiCase


@tagged("post_install", "-at_install")
class TestBulleCourrielClasse(MobileApiCase):

    def _bundle(self):
        return self.env["ir.qweb"]._get_asset_bundle("web.assets_backend")

    def test_le_correctif_est_dans_le_paquet(self):
        urls = {str(f.get("url")) for f in self._bundle().files}
        self.assertIn("/bf_email_management/static/src/js/bf_email_chatter_bubble.js", urls)
        self.assertIn("/bf_email_management/static/src/scss/bf_email_chatter_bubble.scss", urls)
        js = self._bundle().js().raw.decode()
        self.assertIn("@bf_email_management/js/bf_email_chatter_bubble", js)

    def test_les_marges_se_compilent(self):
        css = "".join(a.raw.decode() for a in self._bundle().css())
        self.assertIn(".o-mail-Message-bubble ~ .o-mail-Message-body.o-note", css)

    def test_le_classement_reste_une_note(self):
        """Le geste de « Lier à un dossier » pose toujours une note interne."""
        import inspect
        from odoo.addons.bf_email_management.models.bf_email import BfEmail
        defaut = inspect.signature(BfEmail._import_into_chatter).parameters["subtype_xmlid"].default
        self.assertEqual(defaut, "mail.mt_note")
