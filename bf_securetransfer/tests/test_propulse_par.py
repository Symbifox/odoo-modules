"""« Propulsé par » : un seul nom, celui de la marque, sur les trois surfaces.

La page publique et les quatre courriels lisaient le nom PUBLIC de la
société servie (`appointment_brand_name`, puis `res.company.name`), le
certificat d'accès son nom LÉGAL. Une marque pouvait ainsi créditer le domaine
d'un tiers, un client se créditer lui-même, et une même marque dire un nom sur
la page et un autre sur le certificat.

Chaque essai donne à la société servie des noms qu'aucune autre source ne
porte, et lit le nom CRÉDITÉ là où il s'imprime : un essai qui chercherait
seulement « Symbifox » dans la page passerait aussi si le nom venait d'ailleurs.

Aucun réseau : S3 est patché, `MailMail.send` neutralisé.
"""
import importlib.util
import os
import re
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

from odoo.addons.bf_securetransfer.models.secure_transfer_brand import (
    POWERED_BY_DEFAULT,
)

S3_MOD = "odoo.addons.bf_securetransfer.models.s3"
MODULE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATION = os.path.join(MODULE_DIR, "migrations", "18.0.1.25.0", "pre-migrate.py")

SERVIE_PUBLIQUE = "Servie-Publique-credit"
SERVIE_LEGALE = "Servie-Legale-credit"
CREDITE = "Credite-credit"

# Ce qui suit la mention, en anglais comme en français.
_MENTION = re.compile(r"(?:Powered by|Propulsé par)\s+([^<.\s][^<]*?)\s*(?:<|\.\s*<|\.$)")


def _credits(html):
    return [m.strip().rstrip(".") for m in _MENTION.findall(html or "")]


@tagged("post_install", "-at_install")
class TestPropulseParUneSeuleSource(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        icp = cls.env["ir.config_parameter"].sudo()
        icp.set_param("bf_securetransfer.quota_daily_transfers_per_ip", "500")
        icp.set_param("bf_securetransfer.quota_daily_transfers_per_sender", "500")
        icp.set_param("bf_securetransfer.quota_daily_bytes_per_ip_mb", "1000000")
        icp.set_param("bf_securetransfer.require_recipient_otp", "0")
        icp.set_param("bf_securetransfer.require_sender_otp", "0")
        icp.set_param("bf_securetransfer.default_sender_allowlist", "")
        icp.set_param("bf_securetransfer.default_recipient_allowlist", "")
        cls.env["res.lang"].sudo()._activate_lang("en_CA")
        cls.brand = cls.env.ref("bf_securetransfer.brand_default").sudo()
        cls.company = cls.brand.company_id
        valeurs = {"name": SERVIE_LEGALE}
        if "appointment_brand_name" in cls.company._fields:
            valeurs["appointment_brand_name"] = SERVIE_PUBLIQUE
        cls.company.write(valeurs)
        cls.brand.write({"powered_by": True, "powered_by_name": CREDITE,
                         "watermark_downloads": False})

    def setUp(self):
        super().setUp()
        p = patch("odoo.addons.mail.models.mail_mail.MailMail.send",
                  lambda self, *a, **k: True)
        p.start()
        self.addCleanup(p.stop)

    # ------------------------------------------------------------ fixtures
    def _transfert_actif(self, locale="en_CA"):
        Mail = self.env["mail.mail"].sudo()
        avant = Mail.search([])
        t = self.env["secure.transfer"].api_create(self.brand, {
            "sender_name": "Expéditrice Essai",
            "sender_email": "exp-credit@example.com",
            "recipient_emails": "dest-credit@example.com",
            "message": "Bonjour",
            "retention_days": 7,
        }, "203.0.113.10", "essai-credit/1.0", locale)
        t._register_file("rapport.pdf", 4096)
        cles = {f.s3_key: int(f.size) for f in t.file_ids}
        with patch(S3_MOD + ".head_object",
                   side_effect=lambda env, key: {"size": cles[key], "etag": "e"}
                   if key in cles else None):
            t.action_finalize()
        self.assertEqual(t.state, "active")
        return t, Mail.search([]) - avant

    def _certificat(self, transfer):
        return self.env["ir.actions.report"]._render_qweb_html(
            "bf_securetransfer.action_report_access_certificate",
            transfer.ids)[0].decode()

    # --------------------------------------------------------------- essais
    def test_la_page_lit_le_nom_de_la_marque(self):
        """Si ça casse : la page crédite de nouveau la société servie."""
        self.assertEqual(self.brand._visuals()["powered_by_name"], CREDITE)

    def test_les_courriels_creditent_la_marque(self):
        """Si ça casse : les courriels nomment la société servie, pas la
        marque."""
        _t, courriels = self._transfert_actif()
        self.assertTrue(courriels, "l'envoi n'a produit aucun courriel")
        vus = [c for m in courriels for c in _credits(m.body_html)]
        self.assertTrue(vus, "aucune mention « Powered by » dans les courriels")
        self.assertEqual(set(vus), {CREDITE}, vus)

    def test_le_certificat_credite_le_meme_nom_que_la_page(self):
        """Si ça casse : le certificat, pièce opposable, imprime le nom légal
        de la société pendant que la page dit autre chose."""
        t, _courriels = self._transfert_actif()
        self.assertEqual(_credits(self._certificat(t)), [CREDITE])

    def test_mention_eteinte_rien_au_certificat_ni_aux_courriels(self):
        """Si ça casse : une marque blanche imprime un nom qu'elle a éteint.
        Garde de non-régression : la condition existait avant 18.0.1.25.0, elle
        doit survivre au changement de source."""
        self.brand.powered_by = False
        t, courriels = self._transfert_actif()
        self.assertTrue(courriels, "l'envoi n'a produit aucun courriel")
        self.assertEqual(_credits(self._certificat(t)), [])
        self.assertEqual([c for m in courriels for c in _credits(m.body_html)], [])

    def test_une_marque_neuve_credite_symbifox(self):
        """Si ça casse : une page personnelle créée d'office crédite la société."""
        neuve = self.env["secure.transfer.brand"].sudo().create({
            "name": "Marque neuve credit", "company_id": self.company.id})
        self.assertEqual(neuve.powered_by_name, POWERED_BY_DEFAULT)
        self.assertEqual(neuve._visuals()["powered_by_name"], "Symbifox")

    def test_un_nom_vide_retombe_sur_symbifox_jamais_sur_la_societe(self):
        """Si ça casse : vider le champ fait revenir le nom de la société, ou
        imprime « Powered by » suivi de rien."""
        for vide in (False, "", "   "):
            self.brand.powered_by_name = vide
            self.assertEqual(self.brand._powered_by_label(), "Symbifox", repr(vide))
            self.assertEqual(self.brand._visuals()["powered_by_name"], "Symbifox")

    # ------------------------------------------------------------ migration
    def _migration(self):
        spec = importlib.util.spec_from_file_location(
            "odoo.addons.bf_securetransfer.migrations.pre_migrate_propulse_par", MIGRATION)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_migration_remplit_le_vide_et_garde_un_nom_choisi(self):
        """Si ça casse : la montée écrase un nom réglé à la main, ou laisse une
        marque sans nom, ou bascule une marque sans l'écrire au journal."""
        vide = self.brand
        choisie = self.env["secure.transfer.brand"].sudo().create({
            "name": "Choisie credit", "company_id": self.company.id,
            "powered_by_name": "Choisi à la main"})
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE secure_transfer_brand SET powered_by_name = NULL WHERE id = %s",
            (vide.id,))
        with self.assertLogs(
                "odoo.addons.bf_securetransfer.migrations", level="INFO") as journal:
            module = self._migration()
            module.migrate(self.env.cr, "18.0.1.24.0")
        self.env.invalidate_all()
        self.assertEqual(vide.powered_by_name, "Symbifox")
        self.assertEqual(choisie.powered_by_name, "Choisi à la main")
        lignes = "\n".join(journal.output)
        # Ce qu'affichait la marque avant, page et certificat, est consigné.
        attendu_page = (SERVIE_PUBLIQUE if "appointment_brand_name" in self.company._fields
                        else SERVIE_LEGALE)
        self.assertIn("marque %s " % vide.id, lignes)
        self.assertIn("page « %s »" % attendu_page, lignes)
        self.assertIn("certificat « %s »" % SERVIE_LEGALE, lignes)
        self.assertNotIn("marque %s " % choisie.id, lignes)
        # Rejouée, elle ne touche plus rien.
        with self.assertLogs(
                "odoo.addons.bf_securetransfer.migrations", level="INFO") as second:
            module.migrate(self.env.cr, "18.0.1.24.0")
        self.assertIn("0 marque(s)", "\n".join(second.output))
