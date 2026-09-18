"""Ce que la page promet, éprouvé par HTTP dans le rôle de chaque personne.

Les essais passent par les routes, pas par les méthodes : c'est le trajet réel
— session, droits, charge JSON — et c'est le seul qui prouve qu'un compte sans
droit est arrêté avant d'écrire quoi que ce soit.
"""

import base64
import json

from odoo.tests import tagged
from odoo.tests.common import HttpCase

#: Un JPEG minuscule mais VRAI : le serveur décide du type sur les octets, donc
#: un essai qui enverrait n'importe quoi ne prouverait rien du chemin normal.
JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRof"
    "Hh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAAB"
    "AAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp//2Q=="
)
PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"
HEIC = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x00" * 32


@tagged("post_install", "-at_install")
class EssaiScan(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Users = cls.env["res.users"].with_context(no_reset_password=True)
        groupes = {
            "interne": cls.env.ref("base.group_user"),
            "facture": cls.env.ref("account.group_account_invoice"),
            "enrich": cls.env.ref("bf_contact_enrichment.group_bf_contact_enrich"),
            "partenaire": cls.env.ref("base.group_partner_manager"),
        }
        cls.luc = Users.create({
            "name": "Luc Tout-Droit", "login": "luc_essai",
            "password": "luc_essai", "email": "luc@essai.test",
            "groups_id": [(6, 0, [g.id for g in groupes.values()])],
        })
        cls.mia = Users.create({
            "name": "Mia Interne", "login": "mia_essai",
            "password": "mia_essai", "email": "mia@essai.test",
            "groups_id": [(6, 0, [groupes["interne"].id])],
        })
        cls.contact = cls.env["res.partner"].create({
            "name": "Papeterie de l'essai", "is_company": True,
        })

    # ── Outils ──────────────────────────────────────────────────────

    def appeler(self, route, params):
        reponse = self.url_open(
            route, data=json.dumps({"jsonrpc": "2.0", "method": "call",
                                    "params": params}),
            headers={"Content-Type": "application/json"}, timeout=120)
        self.assertEqual(reponse.status_code, 200, route)
        charge = reponse.json()
        self.assertNotIn("error", charge, charge.get("error"))
        return charge["result"]

    @staticmethod
    def b64(octets):
        return base64.b64encode(octets).decode()

    def pieces_de(self, modele, res_id):
        return self.env["ir.attachment"].search([
            ("res_model", "=", modele), ("res_id", "=", res_id)])

    # ── L'accueil ───────────────────────────────────────────────────

    def test_tuiles_selon_le_role(self):
        """Une tuile n'apparaît qu'au compte qui peut faire le geste."""
        self.authenticate("luc_essai", "luc_essai")
        page = self.url_open("/scan").text
        self.assertIn("/scan/carte", page)
        self.assertIn("/scan/facture", page)
        self.assertIn("/scan/document", page)

        self.authenticate("mia_essai", "mia_essai")
        page = self.url_open("/scan").text
        self.assertIn("/scan/document", page)
        self.assertNotIn("/scan/facture", page)
        self.assertNotIn("/scan/carte", page)

    def test_page_carte_reste_servie(self):
        """La page des cartes a déménagé sous /scan/carte, sans rien perdre."""
        self.authenticate("luc_essai", "luc_essai")
        page = self.url_open("/scan/carte").text
        self.assertIn("scan.js", page)
        self.assertIn("review-form", page)

    # ── La facture ──────────────────────────────────────────────────

    def test_facture_depose_un_brouillon(self):
        self.authenticate("luc_essai", "luc_essai")
        avant = self.env["account.move"].search_count(
            [("move_type", "=", "in_invoice")])
        res = self.appeler("/scan/facture/deposer",
                           {"image_b64": self.b64(JPEG), "filename": "recu.jpg"})
        self.assertNotIn("error", res, res)
        facture = self.env["account.move"].browse(res["move_id"])
        self.assertEqual(facture.move_type, "in_invoice")
        self.assertEqual(facture.state, "draft")
        self.assertEqual(facture.journal_id.type, "purchase")
        self.assertEqual(
            self.env["account.move"].search_count(
                [("move_type", "=", "in_invoice")]), avant + 1)

        pieces = self.pieces_de("account.move", facture.id)
        self.assertEqual(len(pieces), 1)
        self.assertEqual(pieces.mimetype, "image/jpeg")
        # La pièce doit être DANS le fil : posée avec res_id seulement, elle
        # n'apparaîtrait pas là où les gens regardent.
        messages = self.env["mail.message"].search([
            ("model", "=", "account.move"), ("res_id", "=", facture.id)])
        self.assertTrue(any(pieces.id in m.attachment_ids.ids for m in messages))

    def test_facture_ne_marque_jamais_une_piece_illisible(self):
        """🔴 Le cœur du dessin, et il tient dans les DEUX mondes.

        Le passage horaire de ``bf_invoice_ocr`` ne reprend que les factures à
        ``ocr_state = none``. Une pièce que la lecture installée ne sait pas
        lire doit donc rester à ``none`` : la marquer ``error`` la rendrait
        invisible pour toujours, y compris le jour où le module apprendra à la
        lire. Et quand la lecture SAIT lire, la page ne doit pas inventer
        d'état : elle rend celui que le module a écrit.

        L'essai interroge le module plutôt que de figer l'un des deux mondes —
        ``bf_invoice_ocr`` a déjà changé d'avis sur les images en cours de
        route, et un essai figé aurait cassé ce jour-là en accusant la
        mauvaise moitié.
        """
        self.authenticate("luc_essai", "luc_essai")
        res = self.appeler("/scan/facture/deposer",
                           {"image_b64": self.b64(JPEG)})
        facture = self.env["account.move"].browse(res["move_id"])
        if "ocr_state" not in facture._fields:
            self.assertEqual(res["lecture"], "absent")
            return
        if res["lecture"] == "en_attente":
            # Rien n'a été lu : la facture DOIT rester reprenable par le
            # passage horaire, qui ne ramasse que les « none ».
            self.assertEqual(facture.ocr_state, "none")
        else:
            self.assertEqual(res["lecture"], facture.ocr_state,
                             "la page rend l'état du module, jamais le sien")
            self.assertIn(facture.ocr_state, ("done", "error"))
        # Un « error » ne se pose que si le modèle a vraiment répondu.
        if facture.ocr_state == "error":
            self.assertTrue(facture.ocr_raw_response,
                            "une panne passagère ne doit pas geler la facture")

    def test_facture_refusee_sans_le_droit(self):
        self.authenticate("mia_essai", "mia_essai")
        avant = self.env["account.move"].search_count(
            [("move_type", "=", "in_invoice")])
        res = self.appeler("/scan/facture/deposer",
                           {"image_b64": self.b64(JPEG)})
        self.assertIn("error", res)
        self.assertEqual(
            self.env["account.move"].search_count(
                [("move_type", "=", "in_invoice")]), avant,
            "un refus ne doit rien laisser derrière lui")

    def test_facture_refusee_sans_le_groupe_meme_avec_le_droit(self):
        """Le groupe garde la page, pas seulement l'ACL du modèle.

        Sans cet essai, la garde de groupe paraît éprouvée alors que c'est
        l'ACL d'Odoo qui arrête tout le monde : un compte interne ordinaire
        n'a de toute façon pas le droit de créer une facture. On fabrique donc
        le cas qui sépare vraiment les deux — quelqu'un qui PEUT créer une
        facture par un autre groupe, mais qui ne fait pas de facturation.
        """
        groupe = self.env["res.groups"].create({"name": "Essai — créer des factures"})
        self.env["ir.model.access"].create({
            "name": "essai account.move",
            "model_id": self.env.ref("account.model_account_move").id,
            "group_id": groupe.id,
            "perm_read": True, "perm_write": True, "perm_create": True,
            "perm_unlink": False,
        })
        colin = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Colin Sans Facturation", "login": "colin_essai",
            "password": "colin_essai", "email": "colin@essai.test",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id, groupe.id])],
        })
        self.assertTrue(
            self.env["account.move"].with_user(colin).has_access("create"),
            "le montage de l'essai doit vraiment donner le droit de créer")
        self.assertFalse(colin.has_group("account.group_account_invoice"))

        self.authenticate("colin_essai", "colin_essai")
        avant = self.env["account.move"].search_count(
            [("move_type", "=", "in_invoice")])
        res = self.appeler("/scan/facture/deposer",
                           {"image_b64": self.b64(JPEG)})
        self.assertIn("error", res)
        self.assertIn("facturation", res["error"])
        self.assertEqual(
            self.env["account.move"].search_count(
                [("move_type", "=", "in_invoice")]), avant)

    # ── Le document ─────────────────────────────────────────────────

    def test_document_au_tampon_avec_rappel(self):
        self.authenticate("mia_essai", "mia_essai")
        res = self.appeler("/scan/document/deposer", {
            "image_b64": self.b64(JPEG), "filename": "papier.jpg",
            "titre": "Reçu à classer", "destination": "tampon",
            "rappel": "demain",
        })
        note = self.env["bf.note"].browse(res["note_id"])
        self.assertEqual(note.name, "Reçu à classer")
        self.assertEqual(note.user_id, self.mia)
        self.assertEqual(len(self.pieces_de("bf.note", note.id)), 1)
        activites = self.env["mail.activity"].search([
            ("res_model", "=", "bf.note"), ("res_id", "=", note.id)])
        self.assertEqual(len(activites), 1)
        self.assertEqual(activites.user_id, self.mia)
        self.assertTrue(res["rappel"])

    def test_document_au_tampon_sans_rappel(self):
        self.authenticate("mia_essai", "mia_essai")
        res = self.appeler("/scan/document/deposer", {
            "image_b64": self.b64(JPEG), "destination": "tampon",
            "rappel": "aucun",
        })
        note = self.env["bf.note"].browse(res["note_id"])
        self.assertFalse(self.env["mail.activity"].search_count([
            ("res_model", "=", "bf.note"), ("res_id", "=", note.id)]))
        self.assertFalse(res["rappel"])

    def test_document_au_tampon_avec_lien(self):
        self.authenticate("luc_essai", "luc_essai")
        res = self.appeler("/scan/document/deposer", {
            "image_b64": self.b64(JPEG), "destination": "tampon",
            "titre": "Facture du fournisseur",
            "cible": {"model": "res.partner", "id": self.contact.id},
            "rappel": "aujourdhui",
        })
        note = self.env["bf.note"].browse(res["note_id"])
        self.assertEqual(len(note.link_ids), 1)
        self.assertEqual(note.link_ids.res_model, "res.partner")
        self.assertEqual(note.link_ids.res_id, self.contact.id)
        # Le rappel suit le lien : l'activité se pose sur le contact, pas sur
        # la note, parce que c'est là qu'on la verra.
        self.assertTrue(self.env["mail.activity"].search_count([
            ("res_model", "=", "res.partner"), ("res_id", "=", self.contact.id)]))

    def test_document_au_fil_dune_fiche(self):
        self.authenticate("luc_essai", "luc_essai")
        res = self.appeler("/scan/document/deposer", {
            "image_b64": self.b64(JPEG), "filename": "annexe.jpg",
            "titre": "Bon de livraison", "destination": "fiche",
            "cible": {"model": "res.partner", "id": self.contact.id},
        })
        self.assertEqual(res["destination"], "fiche")
        self.assertTrue(res["au_fil"])
        messages = self.env["mail.message"].search([
            ("model", "=", "res.partner"), ("res_id", "=", self.contact.id)])
        avec_piece = messages.filtered(lambda m: m.attachment_ids)
        self.assertTrue(avec_piece)
        self.assertIn("Bon de livraison", avec_piece[0].body)
        # Aucune note n'est née : « au fil » et « au tampon » sont exclusifs.
        self.assertNotIn("note_id", res)

    def test_document_au_fil_exige_la_fiche(self):
        self.authenticate("luc_essai", "luc_essai")
        res = self.appeler("/scan/document/deposer", {
            "image_b64": self.b64(JPEG), "destination": "fiche",
        })
        self.assertIn("error", res)

    def test_document_refuse_une_fiche_illisible(self):
        """Une fiche hors de portée ne se distingue pas d'une fiche absente.

        Le message est le même dans les deux cas, exprès : dire « vous n'y avez
        pas accès » confirmerait l'existence de la fiche à qui n'a pas le droit
        de la voir.
        """
        facture = self.env["account.move"].create({"move_type": "in_invoice"})
        self.authenticate("mia_essai", "mia_essai")
        res = self.appeler("/scan/document/deposer", {
            "image_b64": self.b64(JPEG), "destination": "fiche",
            "cible": {"model": "account.move", "id": facture.id},
        })
        self.assertIn("error", res)
        # Le message doit être CELUI de la fiche invisible, pas celui du droit
        # d'écriture : sinon le contrôle de visibilité peut sauter sans que
        # rien ne tombe, l'autre garde le rattrapant en silence.
        self.assertIn("n'existe plus", res["error"])
        self.assertEqual(len(self.pieces_de("account.move", facture.id)), 0,
                         "un refus ne doit pas laisser de pièce orpheline")

    def test_document_refuse_une_fiche_lisible_mais_non_ecrivable(self):
        """🔴 Lire ne suffit pas : joindre une pièce demande d'ÉCRIRE.

        Le cas qui a échappé au premier jet, mesuré en production :
        ``project.task`` et ``res.partner`` posent tous deux
        ``_mail_post_access = "read"``, donc un compte qui lit la fiche passe
        le seuil du MESSAGE. Mais ``ir.attachment.check()`` contrôle le droit
        d'ÉCRIRE la fiche, et la page se prenait une erreur d'accès brute
        d'Odoo au lieu de sa propre phrase.
        """
        self.assertTrue(
            self.env["res.partner"].with_user(self.mia).has_access("read"))
        self.assertFalse(
            self.env["res.partner"].with_user(self.mia).has_access("write"),
            "le montage de l'essai suppose une fiche lisible et non écrivable")

        avant = self.pieces_de("res.partner", self.contact.id)
        self.authenticate("mia_essai", "mia_essai")
        res = self.appeler("/scan/document/deposer", {
            "image_b64": self.b64(JPEG), "destination": "fiche",
            "cible": {"model": "res.partner", "id": self.contact.id},
        })
        self.assertIn("error", res)
        self.assertIn("pas y écrire", res["error"])
        self.assertEqual(self.pieces_de("res.partner", self.contact.id), avant,
                         "un refus ne doit pas laisser de pièce orpheline")

    def test_selecteur_ne_rend_que_du_lisible(self):
        self.authenticate("luc_essai", "luc_essai")
        res = self.appeler("/scan/document/cibles", {"query": "Papeterie"})
        modeles = {g["model"] for g in res["groupes"]}
        self.assertIn("res.partner", modeles)
        # Deux lettres au minimum : sinon la recherche balaie toute la base à
        # chaque frappe.
        self.assertEqual(self.appeler("/scan/document/cibles",
                                      {"query": "P"})["groupes"], [])

    # ── Ce que la page refuse ───────────────────────────────────────

    def test_types_refuses(self):
        self.authenticate("luc_essai", "luc_essai")
        for charge, attendu in (
            ({"image_b64": ""}, "Aucune image"),
            ({"image_b64": self.b64(b"bonjour")}, "ni une photo"),
            ({"image_b64": self.b64(HEIC)}, "HEIC"),
            ({"image_b64": "pas du base64 !!"}, "illisible"),
        ):
            res = self.appeler("/scan/facture/deposer", charge)
            self.assertIn("error", res, charge)
            self.assertIn(attendu, res["error"])

    def test_pdf_accepte(self):
        """Un PDF passe : c'est le format que la lecture sait déjà lire."""
        self.authenticate("luc_essai", "luc_essai")
        res = self.appeler("/scan/facture/deposer",
                           {"image_b64": self.b64(PDF), "filename": "f.pdf"})
        self.assertNotIn("error", res, res)
        piece = self.pieces_de("account.move", res["move_id"])
        self.assertEqual(piece.mimetype, "application/pdf")

    def test_nom_de_fichier_jamais_un_chemin(self):
        """Un nom vient du client : il ne doit jamais porter de chemin."""
        self.authenticate("luc_essai", "luc_essai")
        res = self.appeler("/scan/facture/deposer", {
            "image_b64": self.b64(JPEG),
            "filename": "../../../etc/passwd",
        })
        piece = self.pieces_de("account.move", res["move_id"])
        self.assertEqual(piece.name, "passwd.jpg")

    # ── L'application installée ─────────────────────────────────────

    def test_manifeste_garde_son_identite(self):
        """🔴 ``id`` ne bouge pas : le changer ferait une deuxième icône."""
        fiche = json.loads(self.url_open("/scan/manifest.webmanifest").text)
        self.assertEqual(fiche["id"], "/scan")
        self.assertEqual(fiche["start_url"], "/scan")
        self.assertEqual(fiche["scope"], "/scan")
        self.assertEqual(fiche["name"], "Numériser")
        self.assertEqual({r["url"] for r in fiche["shortcuts"]},
                         {"/scan/carte", "/scan/facture", "/scan/document"})
        self.assertTrue(all(i["src"].startswith("/bf_scan/static/")
                            for i in fiche["icons"]))

    def test_agent_de_service_couvre_les_deux_modules(self):
        source = self.url_open("/scan/sw.js").text
        self.assertIn("bf-scan-v3", source)
        self.assertIn("/bf_scan/static/src/scan/facture.js", source)
        self.assertIn("/bf_contact_enrichment/static/", source)
        self.assertIn("/bf_scan/static/", source)
