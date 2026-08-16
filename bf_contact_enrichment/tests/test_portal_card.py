"""La page mobile de numérisation, éprouvée par HTTP.

On passe par HTTP et non par l'ORM parce que tout ce qui est propre à cette
page y vit : le décorateur d'authentification, la garde de groupe, la mise en
forme des erreurs pour un téléphone, et les deux en-têtes sans lesquelles la
page n'est pas installable.

L'extraction est simulée. Aucun fournisseur n'est configuré sur un banc, et de
toute façon ce n'est pas la passerelle qu'on éprouve ici : ce qui compte, c'est
que la page traverse l'assistant sans rien réimplémenter.
"""
import base64
import json
from unittest.mock import Mock, patch

from odoo.tests import HttpCase, tagged

# La lecture de carte passe par la passerelle. On la remplace à sa frontière,
# pas plus bas : ce qu'on éprouve ici, c'est la page, et la page ne connaît
# que l'assistant. ``for_feature`` rend un objet, d'où le mandataire.
GATEWAY = "odoo.addons.bf_llm.models.bf_llm.BfLlm.for_feature"


def _gateway_reads(payload):
    """Mandataire de passerelle dont ``extract`` rend l'enveloppe donnée."""
    return Mock(extract=Mock(return_value=payload))

# 1x1 PNG — le contenu n'a aucune importance, la validité du base64 en a une :
# il finit dans une pièce jointe.
PIXEL = base64.b64encode(base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")).decode()

CARD = {
    "full_name": "Maude Fortier",
    "first_name": "Maude",
    "last_name": "Fortier",
    "function": "Directrice des opérations",
    "company": "Ateliers Rivard",
    "email": "maude.fortier@ateliers-rivard.test",
    "phone": "+1 514 555 0142",
    "mobile": "+1 514 555 0188",
    "website": "ateliers-rivard.test",
    "street": "512 rue Bélanger",
    "city": "Montréal",
    "zip": "H2S 1G7",
    "country": "Canada",
    "confidence": 88,
}


@tagged("post_install", "-at_install")
class TestPortalCard(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Users = cls.env["res.users"].with_context(no_reset_password=True)
        enrich_group = cls.env.ref("bf_contact_enrichment.group_bf_contact_enrich")
        # « Création de contacts » est indispensable et n'est PAS impliqué par le
        # groupe d'enrichissement : sur un Odoo d'origine, un usager interne peut
        # lire res.partner mais pas l'écrire. Une porteuse réelle a les deux.
        partner_manager = cls.env.ref("base.group_partner_manager")
        cls.scanner = Users.create({
            "name": "Porteuse de carte",
            "login": "scan.member@test.invalid",
            "password": "scan.member@test.invalid",
            # Sans adresse, message_post refuse de journaliser la création
            # du contact — un usager réel en a une.
            "email": "scan.member@test.invalid",
            "groups_id": [(6, 0, [enrich_group.id, partner_manager.id])],
        })
        # Membre du groupe d'enrichissement, mais sans le droit d'écrire un
        # contact : c'est le trou de configuration le plus probable en clientèle.
        cls.half_rights = Users.create({
            "name": "Droits partiels",
            "login": "scan.half@test.invalid",
            "password": "scan.half@test.invalid",
            # Sans adresse, message_post refuse de journaliser la création
            # du contact — un usager réel en a une.
            "email": "scan.half@test.invalid",
            "groups_id": [(6, 0, [enrich_group.id])],
        })
        cls.outsider = Users.create({
            "name": "Sans droit",
            "login": "scan.outsider@test.invalid",
            "password": "scan.outsider@test.invalid",
            # Sans adresse, message_post refuse de journaliser la création
            # du contact — un usager réel en a une.
            "email": "scan.outsider@test.invalid",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
        })
        cls.Partner = cls.env["res.partner"]

    # ── Outils ──────────────────────────────────────────────────────

    def _rpc(self, path, params):
        """Appelle une route ``type="json"`` comme le fait la page."""
        response = self.url_open(
            path,
            data=json.dumps({"jsonrpc": "2.0", "method": "call",
                             "params": params}).encode(),
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
        self.assertEqual(response.status_code, 200,
                         "une route JSON répond 200 même en cas d'erreur métier")
        return response.json()

    def _ok(self, payload):
        """Déballe un succès, en montrant le refus s'il y en a un.

        Sans ça, un refus de la route se manifeste par un KeyError sur la clé
        attendue, ce qui cache la seule information utile : le message.
        """
        self.assertNotIn("error", payload,
                         "erreur JSON-RPC : %s" % (payload.get("error"),))
        result = payload["result"]
        self.assertNotIn("error", result,
                         "la route a refusé : %s" % (result.get("error"),))
        return result

    def _extract(self, card=None):
        with patch(GATEWAY, return_value=_gateway_reads(
                {"ok": True, "data": dict(card or CARD)})):
            return self._rpc("/scan/extract",
                             {"image_b64": PIXEL, "filename": "carte.png"})["result"]

    def _partners_named(self, name):
        return self.Partner.search([("name", "=", name)])

    # ── La page ─────────────────────────────────────────────────────

    def test_an_anonymous_visitor_is_sent_to_the_login(self):
        response = self.url_open("/scan", timeout=30)
        self.assertIn("/web/login", response.url,
                      "la page ne doit jamais s'ouvrir sans session")

    def test_a_member_gets_the_capture_step(self):
        self.authenticate("scan.member@test.invalid", "scan.member@test.invalid")
        response = self.url_open("/scan", timeout=30)
        self.assertEqual(response.status_code, 200)
        body = response.text
        self.assertIn("step-capture", body)
        # La capture passe par l'app caméra du téléphone : c'est cet attribut
        # qui évite de demander la permission CAMERA à l'usager.
        self.assertIn('capture="environment"', body)
        self.assertIn("/scan/manifest.webmanifest", body)

    def test_a_non_member_is_told_what_is_missing(self):
        self.authenticate("scan.outsider@test.invalid", "scan.outsider@test.invalid")
        response = self.url_open("/scan", timeout=30)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Accès requis", response.text)
        self.assertNotIn("step-capture", response.text)

    def test_a_member_who_cannot_write_contacts_is_stopped_at_the_door(self):
        # Sinon la carte serait lue — donc payée — avant de buter sur le refus
        # d'écriture, ce qui est la pire des deux façons d'échouer.
        self.authenticate("scan.half@test.invalid", "scan.half@test.invalid")
        response = self.url_open("/scan", timeout=30)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Création de contacts", response.text)
        self.assertNotIn("step-capture", response.text)

    def test_a_member_who_cannot_write_contacts_cannot_spend_a_scan(self):
        self.authenticate("scan.half@test.invalid", "scan.half@test.invalid")
        with patch(GATEWAY) as called:
            payload = self._rpc("/scan/extract",
                                {"image_b64": PIXEL, "filename": "carte.png"})
        self.assertIn("error", payload)
        called.assert_not_called()

    # ── Ce qui rend la page installable ─────────────────────────────

    def test_the_manifest_points_back_at_the_page(self):
        response = self.url_open("/scan/manifest.webmanifest", timeout=30)
        self.assertEqual(response.status_code, 200)
        self.assertIn("application/manifest+json",
                      response.headers.get("Content-Type", ""))
        manifest = response.json()
        self.assertEqual(manifest["start_url"], "/scan")
        self.assertEqual(manifest["display"], "standalone")
        sizes = {icon["sizes"] for icon in manifest["icons"]}
        self.assertIn("192x192", sizes)
        self.assertIn("512x512", sizes)
        self.assertIn("maskable", {i["purpose"] for i in manifest["icons"]},
                      "sans icône maskable, le lanceur rogne le glyphe")
        self.assertTrue(manifest.get("id"),
                        "sans identifiant, déplacer start_url installe une "
                        "deuxième icône au lieu de remplacer la première")

    def test_every_icon_the_manifest_declares_is_served(self):
        # Un manifeste qui pointe vers une icône absente ne lève rien : le
        # navigateur cesse simplement de considérer la page installable. Le
        # renommage d'un fichier d'icône se voit donc ici, et nulle part ailleurs.
        manifest = self.url_open("/scan/manifest.webmanifest", timeout=30).json()
        for icon in manifest["icons"]:
            response = self.url_open(icon["src"], timeout=30)
            self.assertEqual(response.status_code, 200, icon["src"])
            self.assertIn("image/png", response.headers.get("Content-Type", ""),
                          icon["src"])

    def test_the_manifest_is_readable_without_a_session(self):
        # Un manifeste est récupéré sans témoin d'authentification : s'il
        # exigeait une session, la page cesserait d'être installable en silence.
        response = self.url_open("/scan/manifest.webmanifest", timeout=30)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("/web/login", response.url)

    def test_the_worker_may_claim_the_page_itself(self):
        response = self.url_open("/scan/sw.js", timeout=30)
        self.assertEqual(response.status_code, 200)
        self.assertIn("javascript", response.headers.get("Content-Type", ""))
        # Servi depuis /scan/sw.js, un worker ne couvrirait que /scan/ — donc
        # pas /scan, la page de départ. Cette en-tête est ce qui l'élargit.
        self.assertEqual(response.headers.get("Service-Worker-Allowed"), "/scan")

    def test_the_worker_answers_a_navigation_once_the_network_is_gone(self):
        # Chromium ne propose l'installation que si l'URL de départ rend un 200
        # hors ligne, et c'est au worker qu'il le demande. Un worker qui déclare
        # un gestionnaire fetch sans jamais répondre à une navigation échoue à
        # ce contrôle en silence : aucune invite, et le navigateur retombe sur
        # un signet. On ne peut pas exécuter le worker ici, faute de moteur JS —
        # on éprouve donc les deux propriétés dont dépend le contrôle.
        source = self.url_open("/scan/sw.js", timeout=30).text
        self.assertIn("offline.html", source,
                      "la coquille hors ligne doit être préchargée")
        self.assertIn("navigate", source,
                      "le worker doit reconnaître une navigation")

    def test_the_offline_shell_is_served_and_says_nothing_personal(self):
        response = self.url_open(
            "/bf_contact_enrichment/static/src/scan/offline.html", timeout=30)
        self.assertEqual(response.status_code, 200)
        # Cette page est mise en cache sur l'appareil : elle doit rester la même
        # pour tout le monde. Ni nom, ni session, ni champ d'une carte.
        self.assertNotIn("t-esc", response.text)
        self.assertIn("Hors ligne", response.text)

    # ── Lecture ─────────────────────────────────────────────────────

    def test_a_scan_returns_the_fields_without_writing_anything(self):
        self.authenticate("scan.member@test.invalid", "scan.member@test.invalid")
        before = self.Partner.search_count([])
        result = self._extract()
        self.assertTrue(result["wizard_id"])
        self.assertEqual(result["fields"]["email"], CARD["email"])
        self.assertEqual(result["fields"]["function"], CARD["function"])
        self.assertEqual(result["confidence"], 88)
        self.assertIsNone(result["match"])
        self.assertEqual(result["mode"], "create")
        self.assertEqual(self.Partner.search_count([]), before,
                         "lire une carte ne doit créer aucun contact")

    def test_a_gateway_failure_reaches_the_phone_as_a_sentence(self):
        self.authenticate("scan.member@test.invalid", "scan.member@test.invalid")
        # La passerelle ne lève pas sur une panne de transport : elle rend une
        # enveloppe portant ``error``. C'est ce cas-là qu'il faut simuler, pas
        # une exception, sans quoi le test éprouverait un chemin qui n'existe pas.
        with patch(GATEWAY, return_value=_gateway_reads(
                {"error": "fournisseur injoignable"})):
            payload = self._rpc("/scan/extract",
                                {"image_b64": PIXEL, "filename": "carte.png"})
        error = payload["result"]["error"]
        # L'assistant enrobe déjà la panne dans un UserError lisible et la page
        # le laisse passer tel quel. Ce qui compte ici : une phrase, jamais une
        # trace de pile.
        self.assertIsInstance(error, str)
        self.assertNotIn("Traceback", error)
        self.assertIn("Lecture impossible", error)

    def test_an_empty_image_is_refused_before_the_gateway(self):
        self.authenticate("scan.member@test.invalid", "scan.member@test.invalid")
        with patch(GATEWAY) as called:
            payload = self._rpc("/scan/extract", {"image_b64": "", "filename": "x.jpg"})
        self.assertIn("error", payload["result"])
        called.assert_not_called()

    # ── Écriture ────────────────────────────────────────────────────

    def test_saving_keeps_the_corrections_made_on_the_phone(self):
        self.authenticate("scan.member@test.invalid", "scan.member@test.invalid")
        scan = self._extract()
        fields = dict(scan["fields"])
        fields["email"] = "m.fortier@ateliers-rivard.test"   # corrigé à l'écran
        fields["function"] = "Directrice générale"

        saved = self._ok(self._rpc("/scan/save", {
            "wizard_id": scan["wizard_id"], "fields": fields, "mode": "create",
        }))

        self.assertFalse(saved.get("updated"))
        partner = self.Partner.browse(saved["partner_id"])
        self.assertEqual(partner.name, "Maude Fortier")
        self.assertEqual(partner.email, "m.fortier@ateliers-rivard.test")
        self.assertEqual(partner.function, "Directrice générale")
        self.assertEqual(partner.city, "Montréal")

    def test_the_card_image_is_filed_on_the_contact(self):
        self.authenticate("scan.member@test.invalid", "scan.member@test.invalid")
        scan = self._extract()
        saved = self._ok(self._rpc("/scan/save", {
            "wizard_id": scan["wizard_id"], "fields": scan["fields"],
            "mode": "create",
        }))
        attachments = self.env["ir.attachment"].search([
            ("res_model", "=", "res.partner"),
            ("res_id", "=", saved["partner_id"]),
        ])
        self.assertTrue(attachments, "la carte doit rester jointe au contact")

    def test_a_known_contact_is_offered_as_an_update(self):
        existing = self.Partner.create({
            "name": "Maude Fortier",
            "email": CARD["email"],
        })
        self.authenticate("scan.member@test.invalid", "scan.member@test.invalid")
        scan = self._extract()
        self.assertIsNotNone(scan["match"], "le doublon doit être détecté avant l'écriture")
        self.assertEqual(scan["match"]["id"], existing.id)
        self.assertEqual(scan["mode"], "update")

        before = self.Partner.search_count([])
        saved = self._ok(self._rpc("/scan/save", {
            "wizard_id": scan["wizard_id"], "fields": scan["fields"],
            "mode": "update",
        }))

        self.assertTrue(saved["updated"])
        self.assertEqual(saved["partner_id"], existing.id)
        self.assertEqual(self.Partner.search_count([]), before,
                         "mettre à jour ne doit pas créer un second contact")
        self.assertEqual(existing.function, CARD["function"],
                         "les champs vides se remplissent")

    def test_the_reviewer_may_override_the_detected_duplicate(self):
        existing = self.Partner.create({
            "name": "Maude Fortier", "email": CARD["email"],
        })
        self.authenticate("scan.member@test.invalid", "scan.member@test.invalid")
        scan = self._extract()
        saved = self._ok(self._rpc("/scan/save", {
            "wizard_id": scan["wizard_id"], "fields": scan["fields"],
            "mode": "create",
        }))
        self.assertNotEqual(saved["partner_id"], existing.id,
                            "le choix fait à l'écran prime sur la détection")

    # ── Garde de groupe ─────────────────────────────────────────────

    def test_a_non_member_cannot_spend_a_scan(self):
        self.authenticate("scan.outsider@test.invalid", "scan.outsider@test.invalid")
        with patch(GATEWAY) as called:
            payload = self._rpc("/scan/extract",
                                {"image_b64": PIXEL, "filename": "carte.png"})
        self.assertIn("error", payload, "la garde de groupe remonte en erreur JSON-RPC")
        called.assert_not_called()

    def test_a_non_member_cannot_write_a_contact(self):
        self.authenticate("scan.member@test.invalid", "scan.member@test.invalid")
        scan = self._extract()
        self.authenticate("scan.outsider@test.invalid", "scan.outsider@test.invalid")
        before = self.Partner.search_count([])
        payload = self._rpc("/scan/save", {
            "wizard_id": scan["wizard_id"], "fields": scan["fields"],
            "mode": "create",
        })
        self.assertIn("error", payload)
        self.assertEqual(self.Partner.search_count([]), before)

    def test_an_expired_scan_asks_for_a_new_photo(self):
        self.authenticate("scan.member@test.invalid", "scan.member@test.invalid")
        payload = self._rpc("/scan/save", {
            "wizard_id": 999999999, "fields": {}, "mode": "create",
        })
        self.assertIn("expiré", payload["result"]["error"])
