"""L'essai hoot du formateur des voix (`static/tests/votes_field.test.js`).

Il tourne dans un navigateur, comme la suite `WebSuite.test_unit_desktop` de
`web`, filtrée sur la seule suite du module. Là où les essais n'ont ni Chrome ni
le paquet websocket-client, Odoo SAUTE cet essai (SkipTest) : un « ok » du
journal n'en dit alors rien, chercher « skipped ».
"""
from odoo.tests import HttpCase, tagged

from odoo.addons.web.tests import test_js

SUITE = "@bf_property_governance"


@tagged("post_install", "-at_install")
class TestVotesFieldJs(HttpCase):

    def test_votes_formatter_hoot(self):
        # Le filtre d'Odoo : un condensé du nom de la suite (`HOOTCommon._generate_hash`).
        suite = test_js.HOOTCommon._generate_hash(None, SUITE)
        self.browser_js(
            "/web/tests?headless&loglevel=2&preset=desktop&timeout=15000&id=%s" % suite,
            "",
            "",
            login="admin",
            timeout=600,
            success_signal="[HOOT] Test suite succeeded",
            error_checker=test_js.unit_test_error_checker,
        )
