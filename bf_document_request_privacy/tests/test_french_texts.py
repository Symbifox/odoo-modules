"""The delivered register texts follow the company's language (18.0.1.1.1).
Same contract as bf_securetransfer_privacy's test, on this bridge's records."""
from odoo.tests import tagged

from odoo.addons.bf_document_request_privacy import hooks as own
from odoo.addons.bf_securetransfer_privacy.tests import test_french_texts as base


@tagged("post_install", "-at_install")
class TestDocumentRequestFrenchTexts(base.TestFrenchTexts):

    # Odoo 18 only runs the test methods a class defines itself, and files a
    # subclass under its base class's module: both are set explicitly here.
    allow_inherited_tests_method = True
    test_module = own.MODULE
    module = own.MODULE

    def _apply(self):
        return own.apply_french_texts(self.env)

    def _french(self):
        return own.FRENCH
