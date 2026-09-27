"""Ce que l'avis doit porter, et ce que le module refuse d'écrire."""
from odoo.exceptions import ValidationError
from odoo.tests.common import tagged

from .common import NoticeCase


@tagged("post_install", "-at_install")
class TestContent(NoticeCase):

    def test_a_notice_takes_its_reference_from_a_sequence(self):
        a = self._notice()
        b = self._notice(lease=a.lease_id)
        self.assertTrue(a.name.startswith("AVIS/"), a.name)
        self.assertNotEqual(a.name, b.name)

    def test_the_max_rent_notice_needs_a_restricted_lease(self):
        """Il n'y a pas de loyer maximal à annoncer sans restriction : l'avis
        n'aurait rien à dire."""
        with self.assertRaises(ValidationError):
            self._notice(kind="new_tenant_max", target_date=False)

    def test_the_max_rent_notice_passes_on_a_restricted_lease(self):
        lease = self._lease(fixation_restricted=True, restriction_kind="new",
                            ready_date="2025-03-01", max_rent_5y=1400.0)
        notice = self._notice(lease=lease, kind="new_tenant_max",
                              target_date=False)
        self.assertTrue(notice)

    def test_notices_are_walled_off_between_companies(self):
        """Un avis nomme le locataire et dit ce qu'on lui demande : même
        sensibilité que le bail, donc même cloisonnement."""
        other = self.env["res.company"].create({"name": "Ailleurs avis inc."})
        notice = self._notice()
        outsider = self.env["res.users"].create({
            "name": "gestionnaire ailleurs", "login": "qa_outsider_notice",
            "company_id": other.id, "company_ids": [(6, 0, [other.id])],
            "groups_id": [(6, 0, [
                self.env.ref("base.group_user").id,
                self.env.ref("bf_property_core.group_bf_property_manager").id,
            ])],
        })
        visible = self.env["bf.rental.notice"].with_user(outsider).search([])
        self.assertNotIn(notice, visible)

    def test_the_moratorium_is_not_walled_off_by_company(self):
        """⚠️ L'état d'une loi du Québec ne dépend pas de la société qui le lit.
        Une règle société ici forcerait chaque société à tenir son propre constat
        du même texte, et elles divergeraient."""
        rules = self.env["ir.rule"].search(
            [("model_id.model", "=", "bf.rental.moratorium")])
        self.assertFalse(rules)
