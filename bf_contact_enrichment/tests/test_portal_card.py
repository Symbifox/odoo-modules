"""La page mobile de numérisation, éprouvée par HTTP.

On passe par HTTP et non par l'ORM parce que tout ce qui est propre à cette
page y vit : le décorateur d'authentification, la garde de groupe, la mise en
forme des erreurs pour un téléphone, et les deux en-têtes sans lesquelles la
page n'est pas installable.

L'extraction est simulée au niveau du TRANSPORT du pont, et pas plus haut :
tout ce qui est au-dessus — l'assistant, le raccourci ``call_bridge`` et
l'estampille du locataire — tourne donc pour de vrai, et la charge envoyée est
observable. C'est elle qui porte l'information qu'une réponse ne montre jamais :
sur quel abonnement l'appel sera facturé.
"""
import base64
import json
import re
from unittest.mock import patch

from odoo.addons.bf_contact_enrichment.controllers import portal_card

from odoo.tests import HttpCase, TransactionCase, tagged

# La lecture de carte part sur la socket du pont. On remplace l'envoi lui-même,
# donc le dernier geste avant le réseau : ce qui est au-dessus est éprouvé, et
# ``called.call_args`` donne la charge telle qu'elle serait partie.
GATEWAY = "odoo.addons.bf_ai_bridge.tools.transport.post"

#: Position de ``payload`` dans ``transport.post(socket, endpoint, payload, …)``.
_CHARGE = 2

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


def url_carte(env):
    """Le chemin de la page des cartes, selon ce que la base porte.

    Seule, cette page EST ``/scan``. Avec ``bf_scan`` installé, ``/scan``
    devient l'accueil à trois tuiles et la page des cartes vit sous
    ``/scan/carte`` — mêmes routes JSON, même script, même gabarit. L'essai
    suit la base plutôt que de figer l'un des deux mondes : sans ça, il
    tomberait sur tout locataire qui installe l'extension, et ce serait
    l'essai qui aurait tort.
    """
    module = env["ir.module.module"].sudo().search(
        [("name", "=", "bf_scan")], limit=1)
    return "/scan/carte" if module.state == "installed" else "/scan"


@tagged("post_install", "-at_install")
class TestPortalCard(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Les textes de la page se lisent dans la langue de l'usager : ces essais
        # les attendent en anglais, sur une base fr_CA seule (BF) comme sur une
        # base neuve ; le français est éprouvé dans TestPortalCardLangue.
        cls.env["res.lang"]._activate_lang("en_US")
        Users = cls.env["res.users"].with_context(no_reset_password=True)
        enrich_group = cls.env.ref("bf_contact_enrichment.group_bf_contact_enrich")
        # « Création de contacts » est indispensable et n'est PAS impliqué par le
        # groupe d'enrichissement : sur un Odoo d'origine, un usager interne peut
        # lire res.partner mais pas l'écrire. Une porteuse réelle a les deux.
        partner_manager = cls.env.ref("base.group_partner_manager")
        cls.scanner = Users.create({
            "name": "Porteuse de carte",
            "lang": "en_US",
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
            "lang": "en_US",
            "login": "scan.half@test.invalid",
            "password": "scan.half@test.invalid",
            # Sans adresse, message_post refuse de journaliser la création
            # du contact — un usager réel en a une.
            "email": "scan.half@test.invalid",
            "groups_id": [(6, 0, [enrich_group.id])],
        })
        cls.outsider = Users.create({
            "name": "Sans droit",
            "lang": "en_US",
            "login": "scan.outsider@test.invalid",
            "password": "scan.outsider@test.invalid",
            # Sans adresse, message_post refuse de journaliser la création
            # du contact — un usager réel en a une.
            "email": "scan.outsider@test.invalid",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
        })
        cls.Partner = cls.env["res.partner"]
        # Sans locataire déclaré, le pont refuse de partir plutôt que de deviner
        # — c'est voulu, et ça rendrait toute lecture impossible sur un banc.
        cls.env["ir.config_parameter"].sudo().set_param(
            "bf_ai_bridge.tenant", "banc")

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
        with patch(GATEWAY, return_value={"data": dict(card or CARD)}):
            return self._rpc("/scan/extract",
                             {"image_b64": PIXEL, "filename": "carte.png"})["result"]

    def _pose_marque(self, valeur):
        """Écrit la couleur de marque de la société, ou saute le test si cette
        base ne porte pas le champ : il vit dans un module de marque qui n'est
        pas une dépendance d'ici."""
        Company = self.env["res.company"]
        if "report_brand_primary" not in Company._fields:
            self.skipTest("cette base ne porte pas les champs de marque")
        self.env.company.sudo().report_brand_primary = valeur

    def _partners_named(self, name):
        return self.Partner.search([("name", "=", name)])

    # ── La page ─────────────────────────────────────────────────────

    def test_an_anonymous_visitor_is_sent_to_the_login(self):
        response = self.url_open(url_carte(self.env), timeout=30)
        self.assertIn("/web/login", response.url,
                      "la page ne doit jamais s'ouvrir sans session")

    def test_a_member_gets_the_capture_step(self):
        self.authenticate("scan.member@test.invalid", "scan.member@test.invalid")
        response = self.url_open(url_carte(self.env), timeout=30)
        self.assertEqual(response.status_code, 200)
        body = response.text
        self.assertIn("step-capture", body)
        # La capture passe par l'app caméra du téléphone : c'est cet attribut
        # qui évite de demander la permission CAMERA à l'usager.
        self.assertIn('capture="environment"', body)
        self.assertIn("/scan/manifest.webmanifest", body)

    def test_a_non_member_is_told_what_is_missing(self):
        self.authenticate("scan.outsider@test.invalid", "scan.outsider@test.invalid")
        response = self.url_open(url_carte(self.env), timeout=30)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Access required", response.text)
        self.assertNotIn("step-capture", response.text)

    def test_a_member_who_cannot_write_contacts_is_stopped_at_the_door(self):
        # Sinon la carte serait lue — donc payée — avant de buter sur le refus
        # d'écriture, ce qui est la pire des deux façons d'échouer.
        self.authenticate("scan.half@test.invalid", "scan.half@test.invalid")
        response = self.url_open(url_carte(self.env), timeout=30)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Contact Creation", response.text)
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

    def test_the_worker_refreshes_its_shell_and_keeps_the_anchors_of_bf_scan(self):
        """Le module `bf_scan` réécrit cet agent par `str.replace` sur trois ancres : un
        changement de forme les rend inopérantes SANS erreur (relecture adverse, tour 1).
        Et le shell se recharge du réseau : `addAll` lit le cache HTTP (`/static/` : une semaine)."""
        if self.env["ir.module.module"].sudo().search_count(
                [("name", "=", "bf_scan"), ("state", "=", "installed")]):
            self.skipTest("avec bf_scan, c'est son agent qui est servi")
        source = self.url_open("/scan/sw.js", timeout=30).text
        for ancre in ("'bf-scan-v2'", "  OFFLINE,\n];",
                      "startsWith('/bf_contact_enrichment/static/')) {"):
            self.assertEqual(source.count(ancre), 1, ancre)
        self.assertIn("{cache: 'reload'}", source)

    def test_the_offline_shell_is_served_and_says_nothing_personal(self):
        response = self.url_open(
            "/bf_contact_enrichment/static/src/scan/offline.html", timeout=30)
        self.assertEqual(response.status_code, 200)
        # Cette page est mise en cache sur l'appareil : elle doit rester la même
        # pour tout le monde. Ni nom, ni session, ni champ d'une carte.
        self.assertNotIn("t-esc", response.text)
        self.assertIn("Offline", response.text)
        self.assertIn("Hors ligne", response.text, "la coquille se dit deux fois")

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
        # Le pont ne lève pas sur une lecture ratée : il rend une enveloppe
        # portant ``error``. C'est ce cas-là qu'il faut simuler, pas une
        # exception, sans quoi le test éprouverait un chemin qui n'existe pas.
        with patch(GATEWAY, return_value={"error": "lecture impossible"}):
            payload = self._rpc("/scan/extract",
                                {"image_b64": PIXEL, "filename": "carte.png"})
        error = payload["result"]["error"]
        # L'assistant enrobe déjà la panne dans un UserError lisible et la page
        # le laisse passer tel quel. Ce qui compte ici : une phrase, jamais une
        # trace de pile.
        self.assertIsInstance(error, str)
        self.assertNotIn("Traceback", error)
        self.assertIn("Could not read the card", error)

    # ── La marque du locataire ──────────────────────────────────────

    def test_the_page_wears_a_colour_a_stylesheet_can_actually_use(self):
        """L'accent part dans du CSS, donc il ne peut pas être n'importe quoi.

        Le champ de marque est libre : une valeur bancale recopiée telle quelle
        casserait la feuille entière, et la page deviendrait illisible sur le
        téléphone de quelqu'un qui n'a rien demandé.
        """
        self.authenticate("scan.member@test.invalid", "scan.member@test.invalid")
        body = self.url_open(url_carte(self.env), timeout=30).text
        accent = re.search(r"--accent:\s*(#[0-9a-fA-F]{6})\s*;", body)
        self.assertIsNotNone(accent, "la page ne pose aucun accent")
        encre = re.search(r"--accent-ink:\s*(#[0-9a-fA-F]{6})\s*;", body)
        self.assertIsNotNone(encre, "l'accent sans son encre rend un bouton illisible")

    def test_the_page_wears_the_brand_this_tenant_declares(self):
        """Le point de tout l'exercice : une page installée sur le téléphone de
        quelqu'un porte SA marque, pas celle de l'éditeur."""
        self._pose_marque("#5c6e5b")
        self.authenticate("scan.member@test.invalid", "scan.member@test.invalid")
        body = self.url_open(url_carte(self.env), timeout=30).text
        self.assertIn("--accent: #5c6e5b;", body)
        self.assertIn("--accent-ink: #ffffff;", body,
                      "une marque foncée garde l'encre presque noire d'origine")

    def test_a_tenant_that_declares_no_brand_keeps_the_page_readable(self):
        self._pose_marque(False)
        self.authenticate("scan.member@test.invalid", "scan.member@test.invalid")
        response = self.url_open(url_carte(self.env), timeout=30)
        self.assertEqual(response.status_code, 200)
        self.assertIn("--accent: %s;" % portal_card.ACCENT_PAR_DEFAUT, response.text)

    def test_a_brand_value_that_is_not_a_colour_never_reaches_the_stylesheet(self):
        """Le champ de marque est libre. Recopié tel quel, il ferme la règle CSS
        et emporte la feuille entière : la page devient illisible sur le
        téléphone de quelqu'un qui n'a rien demandé."""
        self._pose_marque("#c00; } body { display: none")
        self.authenticate("scan.member@test.invalid", "scan.member@test.invalid")
        body = self.url_open(url_carte(self.env), timeout=30).text
        self.assertIn("--accent: %s;" % portal_card.ACCENT_PAR_DEFAUT, body)
        self.assertNotIn("display: none", body)

    def test_a_dark_brand_gets_light_ink_and_the_other_way_round(self):
        """L'encre se calcule, elle ne se recopie pas : l'encre d'origine est
        presque noire et devient invisible dès qu'un locataire déclare une
        marque foncée."""
        self.assertEqual(portal_card._encre_sur("#29abe2"), "#06283a")
        self.assertEqual(portal_card._encre_sur("#5c6e5b"), "#ffffff")
        self.assertEqual(portal_card._encre_sur("#ffffff"), "#06283a")
        self.assertEqual(portal_card._encre_sur("#000000"), "#ffffff")

    def test_anything_that_is_not_a_colour_is_refused(self):
        for valeur in ("", "bleu", "#12345", "#12345g", "red; }", "#1234567"):
            self.assertFalse(portal_card._est_une_couleur(valeur), valeur)
        self.assertTrue(portal_card._est_une_couleur("#00557E"))

    def test_the_call_leaves_under_the_tenant_this_database_declares(self):
        """🔴 Un ``org`` faux ne fait pas échouer l'appel : il le fait réussir
        sur l'abonnement de quelqu'un d'autre, sans rien dire. Le contrôle porte
        donc sur ce qui PART, et il tombe dès qu'un site d'appel écrit un
        locataire en dur.
        """
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_ai_bridge.tenant", "locataire-du-banc")
        self.authenticate("scan.member@test.invalid", "scan.member@test.invalid")
        with patch(GATEWAY, return_value={"data": dict(CARD)}) as called:
            self._rpc("/scan/extract",
                      {"image_b64": PIXEL, "filename": "carte.png"})
        called.assert_called_once()
        args = called.call_args.args
        self.assertEqual(args[1], "/ocr/business-card")
        self.assertEqual(args[_CHARGE].get("org"), "locataire-du-banc",
                         "la carte est partie sur l'abonnement d'un autre")

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
        self.assertIn("expired", payload["result"]["error"])


@tagged("post_install", "-at_install")
class EssaiJournalEnrichissement(TransactionCase):
    """Le résumé d'enrichissement doit arriver au fil comme du HTML.

    🔴 Trouvé sur la seule carte jamais numérisée en production :
    le fil montrait « <p>Contact enrichi via <b>carte d'affaires</b> » en
    toutes lettres, balises comprises.
    """

    def test_le_resume_est_du_html_rendu(self):
        contact = self.env["res.partner"].create({"name": "Fiche d'essai"})
        contact._apply_contact_vals({"function": "Directrice"},
                                    source="carte d'affaires")
        message = self.env["mail.message"].search(
            [("model", "=", "res.partner"), ("res_id", "=", contact.id)],
            order="id desc", limit=1)
        self.assertIn("<b>", message.body)
        self.assertNotIn("&lt;", message.body,
                         "le fil ne doit jamais porter de balise échappée")

    def test_un_libelle_hostile_ne_casse_pas_le_fil(self):
        """Ce qu'on substitue reste échappé, même venant d'une traduction."""
        contact = self.env["res.partner"].create({"name": "Fiche d'essai"})
        corps = contact._enrichment_log_html("<script>x</script>", ["function"])
        self.assertIn("&lt;script&gt;", corps)
        self.assertIn("<li>", corps)


@tagged("post_install", "-at_install")
class TestPortalCardLangue(HttpCase):
    """La page de numérisation se lit dans la langue de l'usager.

    Le script de la page est statique : il ne peut pas se traduire. Ses deux
    messages (session expirée, réponse du serveur) arrivent donc avec la page,
    dans la langue de qui la demande.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for code in ("fr_CA", "en_US"):
            cls.env["res.lang"]._activate_lang(code)
        cls.env["ir.module.module"]._load_module_terms(
            ["bf_contact_enrichment"], ["fr_CA"], overwrite=True)
        Users = cls.env["res.users"].with_context(no_reset_password=True)
        groupe = cls.env.ref("bf_contact_enrichment.group_bf_contact_enrich")
        droits = [groupe.id, cls.env.ref("base.group_partner_manager").id]
        sans_creation = [groupe.id]
        for lang, groupes in (("fr_CA", droits), ("en_US", droits)):
            login = "scan.langue.%s@test.invalid" % lang.lower()
            Users.create({"name": "Lecteur " + lang, "login": login,
                          "password": login, "email": login, "lang": lang,
                          "groups_id": [(6, 0, groupes)]})
        login = "scan.langue.demi@test.invalid"
        Users.create({"name": "Demi-droits", "login": login, "password": login,
                      "email": login, "lang": "fr_CA",
                      "groups_id": [(6, 0, sans_creation)]})

    def _page(self, login):
        self.authenticate(login, login)
        response = self.url_open(url_carte(self.env), timeout=30)
        self.assertEqual(response.status_code, 200)
        return response.text

    def test_every_script_sentence_travels_with_the_page(self):
        """Le script est statique : les dix phrases qu'il montre arrivent avec la page,
        dans la langue du lecteur (``#texts``), et il n'en garde aucune en dur."""
        import re
        attendu = {
            "fr_CA": {"session": "Votre session Odoo a expiré. Rechargez la page.",
                      "server": "Le serveur a répondu %s.",
                      "confidence": "Confiance de lecture : %s %",
                      "saving": "Enregistrement…",
                      "updated": "Contact mis à jour",
                      "created": "Contact créé"},
            "en_US": {"session": "Your Odoo session has expired. Reload the page.",
                      "server": "The server answered %s.",
                      "confidence": "Reading confidence: %s%",
                      "saving": "Saving…",
                      "updated": "Contact updated",
                      "created": "Contact created"},
        }
        cles = {"session", "server", "unexpected", "timeout", "unreadable",
                "notimage", "confidence", "saving", "updated", "created"}
        for lang, textes in attendu.items():
            with self.subTest(lecteur=lang):
                corps = self._page("scan.langue.%s@test.invalid" % lang.lower())
                trouves = dict(re.findall(r'<div data-k="(\w+)">([^<]*)</div>', corps))
                self.assertEqual(set(trouves), cles)
                for cle, texte in textes.items():
                    self.assertEqual(trouves[cle], texte, cle)
                self.assertIn('<html lang="%s"' % lang.replace("_", "-"), corps,
                              "la page déclare la langue de son lecteur")
                self.assertIn('crossorigin="use-credentials"', corps,
                              "le manifeste est demandé avec la session, sinon il ne suit pas la langue")

    def test_the_page_title_follows_the_reader(self):
        """🔴 ``<title>`` n'est jamais traduit par QWeb : le texte passe par un t-set."""
        attendu = {"fr_CA": "<title>Numériser une carte</title>",
                   "en_US": "<title>Scan a card</title>"}
        for lang, titre in attendu.items():
            with self.subTest(lecteur=lang):
                corps = self._page("scan.langue.%s@test.invalid" % lang.lower())
                self.assertIn(titre, corps)

    def test_the_script_has_no_french_left_in_it(self):
        import re
        source = self.url_open(
            "/bf_contact_enrichment/static/src/scan/scan.js", timeout=30).text
        code = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
        code = re.sub(r"//.*", "", code)
        self.assertFalse(re.findall(r".*[àâçéèêëîïôûùÀÉÈÊ].*", code),
                         "une phrase française est restée dans le script")
        for fragment in ("Votre session", "Le serveur a", "carte.jpg", "Confiance",
                         "Enregistre", "Contact créé", "Contact mis à jour"):
            self.assertNotIn(fragment, code)

    def test_the_access_page_follows_the_reader(self):
        corps = self._page("scan.langue.demi@test.invalid")
        self.assertIn("Accès requis", corps)
        self.assertIn("Création de contacts", corps)
        self.assertIn("Enrichissement de contacts", corps)

    def test_the_manifest_follows_the_reader(self):
        """Le nom de l'application installée se lit dans la langue de qui l'installe."""
        if self.env["ir.module.module"].sudo().search_count(
                [("name", "=", "bf_scan"), ("state", "=", "installed")]):
            self.skipTest("avec bf_scan, c'est son manifeste qui est servi")
        attendu = {"fr_CA": ("Numériser une carte", "Carte", "fr-CA"),
                   "en_US": ("Scan a card", "Scan", "en-US")}
        for lang, (nom, court, code) in attendu.items():
            with self.subTest(lecteur=lang):
                login = "scan.langue.%s@test.invalid" % lang.lower()
                self.authenticate(login, login)
                manifest = self.url_open(
                    "/scan/manifest.webmanifest", timeout=30).json()
                self.assertEqual(manifest["name"], nom)
                self.assertEqual(manifest["short_name"], court)
                self.assertEqual(manifest["lang"], code)

