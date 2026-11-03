from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestIrregularityDigest(TransactionCase):
    def test_section_lists_open_irregularities_of_reader(self):
        user = self.env.user
        partner = self.env["res.partner"].create({"name": "Dossier <b>témoin</b>"})
        detector = self.env["bf.irregularity.detector"].create({
            "name": "Témoin", "code": "zz_digest", "family": "late", "default_owner_id": user.id,
        })
        detector._sync([{"record": partner, "message": "Échue & oubliée"}])
        config = self.env["daily.digest.config"].search([], limit=1) or self.env["daily.digest.config"].create({})
        html = config._render_irregularities(user)
        self.assertIn("Témoin", html)
        self.assertIn("Échue &amp; oubliée", html)
        self.assertIn("&lt;b&gt;", html, "le nom du dossier est échappé")
        self.assertIn("/odoo/res.partner/%s" % partner.id, html)
        self.env["bf.irregularity"].search([("detector_id", "=", detector.id)]).action_acknowledge()
        self.assertNotIn("Témoin", config._render_irregularities(user),
                         "un constat pris en compte quitte la section")
        config.include_irregularities = False
        self.assertEqual(config._render_irregularities(user), "")
