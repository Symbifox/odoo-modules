"""The tenant served follows the host Odoo itself kept.

Under proxy_mode, ProxyFix keeps the LAST X-Forwarded-Host (our proxy's).
The controller read the raw header and kept its FIRST element: a client
writing « other, acme » got the other tenant's kickstart."""
from unittest.mock import patch

from odoo.tests import HttpCase, tagged
from odoo.tools import config


@tagged("post_install", "-at_install")
class TestForwardedHost(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Org = cls.env["bf.policy.org"]
        Org.search([]).write({"active": False})
        for name, domain in (("Acme Co.", "acme.example"),
                             ("Other Co.", "other.example")):
            company = cls.env["res.company"].create({"name": name})
            Org.create({
                "company_id": company.id,
                "domain": domain,
                "slug": domain.split(".")[0],
                "oci_image_ref": "ghcr.io/%s/blue-fox-os:latest" % domain.split(".")[0],
                "authentik_device_url": "https://auth.%s/application/o/device/" % domain,
                "authentik_token_url": "https://auth.%s/application/o/token/" % domain,
                "oidc_client_id": "blue-fox-os",
            })
        cls.env.flush_all()

    def _served(self, header):
        with patch.dict(config.options, {"proxy_mode": True}):
            resp = self.url_open("/blue-fox-install.ks",
                                 headers={"X-Forwarded-Host": header})
        self.assertEqual(resp.status_code, 200, resp.text[:300])
        for slug in ("acme", "other"):
            if "ghcr.io/%s/" % slug in resp.text:
                return slug
        return None

    def test_proxy_value_alone(self):
        self.assertEqual(self._served("acme.example"), "acme")

    def test_client_value_in_front_of_proxy_value(self):
        self.assertEqual(self._served("other.example, acme.example"), "acme")
