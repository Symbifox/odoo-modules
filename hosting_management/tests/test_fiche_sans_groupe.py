# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import re

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged

GROUPE = "hosting_management.group_hosting_user"


@tagged("post_install", "-at_install")
class TestFicheSansGroupe(TransactionCase):
    """Un interne sans le groupe hébergement ouvre une fiche contact et un projet.

    Le client web lit tous les champs de la vue, invisibles compris : un seul champ
    vers un modèle hosting.* suffit à fermer le formulaire à qui n'a pas le groupe.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.sans = new_test_user(cls.env, login="essai_sans_groupe", groups="base.group_user,project.group_project_user")
        cls.avec = new_test_user(cls.env, login="essai_avec_groupe", groups=f"base.group_user,project.group_project_user,{GROUPE}")
        cls.client = cls.env["res.partner"].create({"name": "Client Essai Parc", "is_company": True, "has_sla": True})
        cls.env["hosting.endpoint"].create({"name": "Poste Essai", "partner_id": cls.client.id})
        cls.projet = cls.env["project.project"].create({"name": "Projet Essai Parc", "partner_id": cls.client.id})

    def _ouvrir(self, user, record):
        """Ce que fait le client web : la vue du formulaire, puis la lecture de ses champs.

        Rend les champs de la vue et ceux qui lèvent à la lecture. Lu champ par champ :
        un autre module peut fermer le même formulaire (privacy_consent sur le projet,
        hors de ce module) ; l'essai ne juge que les champs posés par celui-ci.
        """
        model = record.with_user(user)
        arch = model.get_views([(False, "form")])["views"]["form"]["arch"]
        noms = set(re.findall(r'<field name="([^"]+)"', arch)) & set(model._fields)
        refus = set()
        for nom in noms:
            try:
                model.read([nom])
            except AccessError:
                refus.add(nom)
            model.invalidate_recordset()
        return noms, {n for n in refus if model._fields[n]._module == "hosting_management"}

    def test_sans_le_groupe_les_formulaires_s_ouvrent(self):
        self.assertNotIn(self.env.ref(GROUPE), self.sans.groups_id)
        # Témoin : sans le groupe, le champ fautif lève bien (sinon l'essai ne prouve rien).
        with self.assertRaises(AccessError):
            self.client.with_user(self.sans).read(["hosting_endpoint_count"])
        noms, refus = self._ouvrir(self.sans, self.client)
        self.assertFalse(refus)
        self.assertFalse({n for n in noms if n.startswith(("hosting_", "voip_")) or n == "has_sla"})
        noms, refus = self._ouvrir(self.sans, self.projet)
        self.assertFalse(refus)
        self.assertNotIn("license_seat_count", noms)

    def test_avec_le_groupe_rien_ne_disparait(self):
        noms, refus = self._ouvrir(self.avec, self.client)
        self.assertFalse(refus)
        for champ in ("has_sla", "hosting_service_count", "hosting_endpoint_count",
                      "hosting_endpoint_ids", "hosting_license_count", "voip_did_count"):
            self.assertIn(champ, noms)
        noms, refus = self._ouvrir(self.avec, self.projet)
        self.assertFalse(refus)
        self.assertIn("license_seat_count", noms)
