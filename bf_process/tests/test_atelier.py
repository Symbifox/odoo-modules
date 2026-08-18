# -*- coding: utf-8 -*-
"""Le tirage d'atelier : codes QR, pavage tabloïd, page portail à jeton."""
from odoo.exceptions import UserError
from odoo.tests import HttpCase, TransactionCase, tagged

from ..generateur import pdf as gen_pdf
from ..generateur import qr
from .test_aller_retour import CARTE


def _pages(octets):
    """Nombre de pages d'un PDF, compté sans bibliothèque."""
    return octets.count(b"/Type /Page\n") or octets.count(b"/Type /Page")


@tagged("post_install", "-at_install")
class TestCodeQr(TransactionCase):

    def test_un_qr_trop_petit_est_agrandi_plutot_que_subi(self):
        """Un QR illisible a l'air parfaitement normal sur l'écran du
        producteur. C'est pour ça qu'on refuse de le rapetisser."""
        adresse = "https://exemple.test/carte/etape/12?access_token=" + "a" * 32
        cote, module = qr.cote_lisible(adresse, 4.0)
        self.assertGreaterEqual(module, qr.MODULE_MINI)
        self.assertGreater(cote, 4.0)

    def test_un_qr_assez_grand_garde_la_taille_demandee(self):
        adresse = "https://exemple.test/c/abc"
        cote, module = qr.cote_lisible(adresse, 300.0)
        self.assertEqual(cote, 300.0)

    def test_le_recouvrement_depasse_un_code_qr(self):
        """Un QR à cheval sur une coupe est tronqué sur les DEUX feuilles.

        La bande commune entre deux tuiles n'est donc pas qu'une marge de
        collage : c'est ce qui garantit que chaque QR tient en entier sur au
        moins une feuille.
        """
        pire = ("https://www.bluefoxconsultant.com/carte/etape/999999"
                "?access_token=" + "8" * 36)
        cote, _module = qr.cote_lisible(pire, gen_pdf.QR_COTE)
        self.assertGreater(gen_pdf.RECOUVREMENT,
                           cote * gen_pdf.ECHELLE_ATELIER,
                           "le recouvrement est plus étroit qu'un code QR")

    def test_a_l_echelle_d_atelier_les_modules_restent_scannables(self):
        """Le seuil vaut sur le PAPIER, pas dans le modèle.

        C'est ce que le contrôle hors serveur a montré : bien formé et illisible
        sont deux choses différentes, et seule la taille physique les sépare.
        """
        pire = ("https://www.bluefoxconsultant.com/carte/etape/999999"
                "?access_token=" + "8" * 36)
        _cote, module = qr.cote_lisible(pire, gen_pdf.QR_COTE)
        mm = module * gen_pdf.ECHELLE_ATELIER * 25.4 / 72
        self.assertGreaterEqual(round(mm, 2), 0.40,
                                "module de %.2f mm : sous le seuil où un "
                                "téléphone accroche" % mm)

    def test_la_matrice_encode_vraiment_l_adresse(self):
        courte = qr.matrice("https://exemple.test/c/a")
        longue = qr.matrice("https://exemple.test/c/" + "a" * 200)
        self.assertGreater(len(longue), len(courte))
        self.assertTrue(all(len(r) == len(courte) for r in courte))


@tagged("post_install", "-at_install")
class TestTirageAtelier(TransactionCase):

    def setUp(self):
        super().setUp()
        self.processus = self.env["bf.process"].create({
            "name": "Essai atelier", "code": "essai", "pool_name": "Blue Fox"})
        self.processus._charger_niveaux(CARTE)
        self.noeuds = {n.code: n for n in self.processus.diagram_ids.node_ids}

    def _ressource(self, noeud):
        return self.env["bf.process.node.resource"].create({
            "node_id": noeud.id, "name": "Comment souder", "kind": "video",
            "url": "https://exemple.test/video.mp4"})

    def test_sans_ressource_le_tirage_est_refuse(self):
        """Le même document en plus de pages n'est pas un tirage d'atelier."""
        with self.assertRaises(UserError):
            self.processus.action_telecharger_pdf_atelier()

    def test_seules_les_etapes_porteuses_ont_un_qr(self):
        self._ressource(self.noeuds["t1"])
        diagrammes = self.processus._dicts_atelier()
        avec = [n for d in diagrammes for n in d["nodes"] if n.get("qr")]
        self.assertEqual([n["id"] for n in avec], ["t1"])
        self.assertIn("/carte/etape/%s" % self.noeuds["t1"].id, avec[0]["qr"])
        self.assertIn("access_token=", avec[0]["qr"])

    def test_une_annotation_ne_porte_jamais_de_qr(self):
        note = self.processus.diagram_ids.node_ids.filtered(
            lambda n: n.kind == "note")
        if not note:
            self.skipTest("la carte d'essai n'a pas d'annotation")
        self.assertNotIn(note[:1], self.processus._noeuds_avec_ressources())

    def test_le_pavage_rend_plusieurs_pages_la_ou_la_page_unique_en_rend_une(self):
        """Une carte plus large qu'un tabloïd doit sortir en plusieurs tuiles."""
        d = self.processus.to_dicts()
        une = gen_pdf.to_pdf(d, titre="Essai")
        pavee = gen_pdf.to_pdf(d, titre="Essai", pave=(200.0, 200.0))
        self.assertEqual(_pages(une), 1)
        self.assertGreater(_pages(pavee), 1)

    def test_le_pavage_ne_change_pas_la_carte_elle_meme(self):
        """Le pavage déplace l'origine, il ne redessine pas.

        La preuve qui compte : les exports XML, eux, ne bougent pas d'un octet
        quand on tire une version d'atelier.
        """
        avant = self.processus.exporter_bpmn()
        self._ressource(self.noeuds["t1"])
        self.processus.action_telecharger_pdf_atelier()
        self.assertEqual(self.processus.exporter_bpmn(), avant)

    def test_le_tirage_produit_bien_un_pdf(self):
        self._ressource(self.noeuds["t1"])
        action = self.processus.action_telecharger_pdf_atelier()
        self.assertEqual(action["type"], "ir.actions.act_url")
        piece = self.env["ir.attachment"].search(
            [("res_model", "=", "bf.process"), ("res_id", "=", self.processus.id)],
            order="id desc", limit=1)
        self.assertIn("-atelier.pdf", piece.name)
        self.assertTrue(piece.raw.startswith(b"%PDF"))


@tagged("post_install", "-at_install")
class TestPortailAtelier(HttpCase):

    def setUp(self):
        super().setUp()
        self.processus = self.env["bf.process"].create({
            "name": "Essai portail", "code": "essai", "pool_name": "Blue Fox"})
        self.processus._charger_niveaux(CARTE)
        self.noeud = self.processus.diagram_ids.node_ids.filtered(
            lambda n: n.code == "t1")
        self.ressource = self.env["bf.process.node.resource"].create({
            "node_id": self.noeud.id, "name": "Fiche du décapant",
            "kind": "fiche", "url": "https://exemple.test/sds.pdf"})
        self.noeud._portal_ensure_token()

    def test_sans_jeton_la_page_refuse(self):
        r = self.url_open("/carte/etape/%s" % self.noeud.id)
        self.assertEqual(r.status_code, 200)
        self.assertIn("ne mène nulle part", r.text)
        self.assertNotIn("Fiche du décapant", r.text)

    def test_avec_un_mauvais_jeton_la_page_refuse(self):
        r = self.url_open("/carte/etape/%s?access_token=%s"
                          % (self.noeud.id, "0" * 32))
        self.assertIn("ne mène nulle part", r.text)
        self.assertNotIn("Fiche du décapant", r.text)

    def test_avec_le_bon_jeton_la_page_montre_les_consignes(self):
        r = self.url_open("/carte/etape/%s?access_token=%s"
                          % (self.noeud.id, self.noeud.access_token))
        self.assertEqual(r.status_code, 200)
        self.assertIn("Fiche du décapant", r.text)
        self.assertIn("Fiche signalétique", r.text)

    def test_un_jeton_valide_ne_sert_pas_la_ressource_d_une_autre_etape(self):
        """Le trou qu'on croit avoir fermé : le jeton porte sur l'étape, donc
        la ressource demandée doit appartenir à CETTE étape.

        ⚠️ La ressource étrangère porte un FICHIER, pas une adresse. Avec une
        adresse, le chemin qui fuit renvoie une redirection que le serveur de
        test sert lui-même en 404 — le test passait alors au vert sur le code
        vulnérable. Un fichier rend 200 et le contenu : l'écart entre fuite et
        refus devient visible.
        """
        import base64
        autre = self.processus.diagram_ids.node_ids.filtered(
            lambda n: n.code == "g")
        piece = self.env["ir.attachment"].create({
            "name": "secret.txt", "mimetype": "text/plain",
            "datas": base64.b64encode(b"CECI-NE-DOIT-PAS-SORTIR")})
        etrangere = self.env["bf.process.node.resource"].create({
            "node_id": autre.id, "name": "Pas la vôtre", "kind": "procedure",
            "attachment_id": piece.id})
        r = self.url_open("/carte/etape/%s/ressource/%s?access_token=%s"
                          % (self.noeud.id, etrangere.id,
                             self.noeud.access_token))
        self.assertNotIn("CECI-NE-DOIT-PAS-SORTIR", r.text)
        self.assertEqual(r.status_code, 404)

    def test_une_ressource_externe_part_vraiment_chez_le_fournisseur(self):
        """`request.redirect` est LOCAL par défaut : sans `local=False`, une
        fiche signalétique hébergée chez le fournisseur devient un chemin
        relatif servi par Odoo, donc un 404."""
        r = self.url_open("/carte/etape/%s/ressource/%s?access_token=%s"
                          % (self.noeud.id, self.ressource.id,
                             self.noeud.access_token),
                          allow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r.headers.get("Location"),
                         "https://exemple.test/sds.pdf")
