# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""La section « Mises à jour du parc » se lit dans la langue du destinataire.

Le digest de base (`daily_todo_digest`) part à chaque destinataire dans SA
langue : `_send_digest` bâtit chaque courriel sous `lang=user.lang`. La section
de ce module était écrite en français dans la source : un destinataire réglé en
anglais la recevait en français au milieu d'un digest anglais. Sa source est
maintenant anglaise, et le français vit dans `i18n/fr_CA.po`.

Les essais passent par le vrai chemin d'envoi, `_send_digest`, et lisent le
`mail.mail` créé ; l'envoi lui-même est neutralisé (rien ne sort). Les langues
sont épinglées sur les destinataires créés ici : sur une base réelle, OdooBot et
l'admin sont en fr_CA.
"""

import uuid

from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestLangue(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for code in ("fr_CA", "en_CA"):
            cls.env["res.lang"]._activate_lang(code)
        cls.env["ir.module.module"]._load_module_terms(
            ["bf_hosting_patch_digest"], ["fr_CA"], overwrite=True)
        cls.config = cls.env["daily.digest.config"].create({
            "name": "Digest langue essai",
            "include_hosting_patch": True,
            "include_weather": False,
            "include_quote": True,
        })
        partenaire = cls.env["res.partner"].create({
            "name": "Client du digest essai", "is_company": True,
        })
        poste = cls.env["hosting.endpoint"].create({
            "name": "essai-01", "partner_id": partenaire.id,
        })
        # Sans relevé, le système est muet : la section paraît toujours.
        cls.systeme = cls.env["bf.patch.system"].create({
            "name": "essai-01-linux", "endpoint_id": poste.id,
            "machine_id": uuid.uuid4().hex, "patch_managed": True,
        })
        groupes = "base.group_user,hosting_management.group_hosting_manager"
        cls.anglophone = new_test_user(
            cls.env, login="essai-digest-en", groups=groupes,
            lang="en_CA", email="essai-digest-en@example.com",
            context={"no_reset_password": True})
        cls.francophone = new_test_user(
            cls.env, login="essai-digest-fr", groups=groupes,
            lang="fr_CA", email="essai-digest-fr@example.com",
            context={"no_reset_password": True})

    def setUp(self):
        super().setUp()
        # Rien ne sort : le courriel créé suffit.
        self.patch(type(self.env["mail.mail"]), "send", lambda self, *a, **k: True)

    def _corps_envoye(self, destinataire):
        """Le corps du digest tel que `_send_digest` le bâtit pour cette personne."""
        # Le travail planifié porte une langue qui n'est pas celle du destinataire.
        autre = "fr_CA" if destinataire.lang == "en_CA" else "en_CA"
        self.config.with_context(lang=autre)._send_digest(test_user=destinataire)
        courriels = self.env["mail.mail"].search(
            [("email_to", "=", destinataire.email)])
        self.assertEqual(len(courriels), 1, "le digest n'a pas été bâti")
        return courriels.body_html

    def test_section_en_anglais_pour_un_destinataire_en_ca(self):
        self.assertEqual(self.systeme.patch_state, "stale")
        corps = self._corps_envoye(self.anglophone)
        self.assertIn("essai-01-linux", corps)
        self.assertIn("Fleet updates", corps)
        self.assertIn("no longer reporting", corps)
        self.assertIn("silent, no recent report", corps)
        self.assertNotIn("Mises à jour du parc", corps)
        self.assertNotIn("ne rapporte", corps)

    def test_section_en_francais_pour_un_destinataire_fr_ca(self):
        corps = self._corps_envoye(self.francophone)
        self.assertIn("essai-01-linux", corps)
        self.assertIn("Mises à jour du parc", corps)
        self.assertIn("ne rapporte", corps)
        self.assertIn("muet, aucun relevé récent", corps)
        self.assertNotIn("Fleet updates", corps)
        self.assertNotIn("no longer reporting", corps)
