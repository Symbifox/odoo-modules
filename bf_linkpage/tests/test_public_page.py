"""L'invariant central du module : un slug qui ne résout pas rend un 404.

Ces tests ne décrivent pas un parcours d'usager, ils verrouillent une DÉCISION.
Le module voisin `bf_appointment` redirige en silence vers son index quand le
slug est inconnu ; on a délibérément fait l'inverse, parce que l'adresse d'une
page de liens part dans un QR imprimé qui ne se corrige plus. Une redirection
donnerait une page qui s'affiche, donc l'apparence du succès, et personne ne
verrait jamais que le QR pointe à côté.

D'où la forme des assertions : elles vérifient le code 404 ET l'absence de
redirection. Un test qui se contenterait de « la bonne page ne s'affiche pas »
passerait aussi avec la redirection silencieuse qu'on refuse — il ne
discriminerait rien.
"""

from datetime import timedelta

from odoo import fields
from odoo.tests import HttpCase, tagged


@tagged("bf_linkpage", "post_install", "-at_install")
class TestPublicPage(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({
            "name": "Page Publique", "email": "page.publique@test.invalid",
        })
        cls.page = cls.env["bf.linkpage"].create({
            "name": "Page Publique",
            "slug": "page-publique",
            "kind": "owner",
            "partner_id": cls.partner.id,
            "state": "published",
        })
        cls.env["bf.linkpage.link"].create({
            "page_id": cls.page.id,
            "name": "Site",
            "source_code": "manual",
            "url": "https://example.invalid/",
        })

    def _get(self, url):
        """Vider le cache ORM, puis ouvrir sans suivre les redirections.

        DEUX précautions, chacune contre un faux résultat distinct.

        Le vidage d'abord : `url_open` ne le fait PAS de lui-même, et le
        travailleur HTTP lit par un autre curseur. Sans vidage, tout ce que le
        test vient de créer ou de modifier lui est invisible, et CHAQUE route
        rend 404. Les assertions « doit rendre 404 » passeraient alors sans
        rien discriminer : elles seraient vertes même si la page ne
        s'affichait jamais, pour personne. C'est `test_page_publiee_repond_200`
        qui les rend significatives, en prouvant que la donnée est bien
        visible du serveur.

        Le non-suivi des redirections ensuite : `url_open` les suit par
        défaut, donc une redirection vers une page répondant 200 se lirait
        comme un succès et masquerait exactement la redirection silencieuse
        que ce module refuse.
        """
        self.env.flush_all()
        # `Accept-Language` est POSÉ, pas laissé au hasard. Le site sert deux
        # langues ; sans en-tête, le client d'essai laisse Odoo négocier et la
        # négociation renvoie un 303 vers `/en/…`. Le test lirait alors une
        # redirection de langue parfaitement légitime comme la redirection
        # SILENCIEUSE que ce module refuse, et il échouerait pour une raison
        # qui n'a rien à voir avec ce qu'il vérifie.
        return self.url_open(
            url,
            allow_redirects=False,
            headers={"Accept-Language": "fr-CA,fr;q=0.9"},
        )

    # ── le cas nominal ───────────────────────────────────────────────────────

    def test_page_publiee_repond_200(self):
        response = self._get("/l/page-publique")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Page Publique", response.text)

    def test_page_publiee_pose_les_entetes_de_securite(self):
        response = self._get("/l/page-publique")
        self.assertEqual(response.headers.get("X-Frame-Options"), "DENY")
        self.assertEqual(response.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertIn("frame-ancestors 'none'", response.headers.get("Content-Security-Policy", ""))

    # ── l'invariant : 404 franc, jamais de redirection ───────────────────────

    def test_slug_inconnu_rend_404_et_pas_une_redirection(self):
        response = self._get("/l/ce-slug-nexiste-pas")
        self.assertEqual(response.status_code, 404)
        self.assertNotIn(response.status_code, (301, 302, 303, 307, 308))

    def test_page_brouillon_rend_404(self):
        self.page.state = "draft"
        response = self._get("/l/page-publique")
        self.assertEqual(response.status_code, 404)

    def test_page_fermee_rend_404(self):
        self.page.state = "closed"
        response = self._get("/l/page-publique")
        self.assertEqual(response.status_code, 404)

    def test_page_archivee_rend_404(self):
        self.page.active = False
        response = self._get("/l/page-publique")
        self.assertEqual(response.status_code, 404)

    def test_page_expiree_rend_404(self):
        self.page.date_expiry = fields.Datetime.now() - timedelta(seconds=1)
        response = self._get("/l/page-publique")
        self.assertEqual(response.status_code, 404)

    def test_tous_les_refus_sont_indiscernables(self):
        """Aucun refus ne doit trahir l'existence du slug.

        Si un slug existant mais non publié rendait autre chose qu'un slug
        inexistant, l'adresse deviendrait un oracle : un visiteur anonyme
        pourrait énumérer les pages en comparant les réponses.
        """
        inconnu = self._get("/l/aucun-slug-de-ce-nom")
        self.page.state = "draft"
        brouillon = self._get("/l/page-publique")
        self.assertEqual(inconnu.status_code, brouillon.status_code)

    # ── la redirection de clic ───────────────────────────────────────────────

    def test_clic_redirige_et_compte(self):
        link = self.page.link_ids[0]
        response = self._get("/l/page-publique/go/%s" % link.id)
        self.assertIn(response.status_code, (302, 303))
        self.assertEqual(response.headers.get("Location"), "https://example.invalid/")
        link.invalidate_recordset()
        self.assertEqual(link.click_count, 1)

    def test_clic_sur_un_lien_d_une_autre_page_rend_404(self):
        """Le lien est cherché DANS la page, pas par son seul identifiant.

        Sans cette contrainte, l'adresse ferait rediriger le site vers
        n'importe quelle URL enregistrée ailleurs, y compris sur une page non
        publiée : une redirection ouverte offerte par un identifiant numérique.
        """
        autre = self.env["bf.linkpage"].create({
            "name": "Autre", "slug": "autre-page", "kind": "owner",
            "partner_id": self.partner.id, "state": "published",
        })
        lien_ailleurs = self.env["bf.linkpage.link"].create({
            "page_id": autre.id, "name": "Ailleurs",
            "source_code": "manual", "url": "https://ailleurs.invalid/",
        })
        response = self._get("/l/page-publique/go/%s" % lien_ailleurs.id)
        self.assertEqual(response.status_code, 404)

    def test_clic_sur_un_lien_inactif_rend_404(self):
        link = self.page.link_ids[0]
        link.active = False
        response = self._get("/l/page-publique/go/%s" % link.id)
        self.assertEqual(response.status_code, 404)

    # ── le QR n'est pas public ───────────────────────────────────────────────

    def test_qr_refuse_a_un_visiteur_anonyme(self):
        """Le QR n'encode qu'une adresse publique, mais le fabriquer coûte du
        calcul : une route publique qui compose une image à la demande est un
        levier commode pour saturer le serveur."""
        response = self._get("/l/page-publique/qr.png")
        self.assertNotEqual(response.status_code, 200)

    def _connecter(self, avec_groupe):
        """Un compte d'essai à nous, avec un mot de passe que NOUS posons.

        `authenticate("admin", "admin")` ne tient que sur une base neuve. Sur
        un banc alimenté depuis une copie de production, le mot de passe de
        l'administrateur n'est pas « admin » : le test sortait en erreur
        d'authentification, ce qui se lit comme un défaut du module alors que
        c'est le harnais qui est faux.
        """
        groupes = [self.env.ref("base.group_user").id]
        if avec_groupe:
            groupes.append(self.env.ref("bf_linkpage.group_bf_linkpage_user").id)
        self.env["res.users"].create({
            "name": "Essai QR",
            "login": "essai.qr@linkpage.invalid",
            "password": "essai-qr-linkpage",
            "email": "essai.qr@linkpage.invalid",
            "groups_id": [(6, 0, groupes)],
        })
        self.env.flush_all()
        # ⚠️ L'authentification de session ne passe pas partout. `auth_totp`
        # installé sur une base ALIMENTÉE PAR COPIE bloque l'ouverture de
        # session par mot de passe, et le test sort alors en erreur
        # d'authentification — ce qui se lit comme un défaut de la route QR
        # alors que la route n'a jamais été appelée. On le dit et on saute.
        try:
            session = self.authenticate("essai.qr@linkpage.invalid", "essai-qr-linkpage")
        except Exception as echec:  # noqa: BLE001
            # `authenticate` LÈVE, elle ne rend pas une valeur fausse : un
            # `if not ...` ne voit jamais le refus et le test sort en erreur.
            self.skipTest(
                "ouverture de session refusée sur cette base (%s) : la route QR "
                "n'est pas vérifiable ici" % type(echec).__name__
            )
        if not session:
            self.skipTest("ouverture de session sans effet : route QR non vérifiable ici")

    def test_qr_rend_un_png_a_un_usager_connecte(self):
        # Le groupe est nécessaire : la route cherche la page SANS `sudo`,
        # donc un usager interne hors du module reçoit un 403. C'est voulu,
        # et c'est ce que vérifie le test suivant.
        self._connecter(avec_groupe=True)
        response = self.url_open("/l/page-publique/qr.png")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("Content-Type"), "image/png")
        self.assertTrue(response.content.startswith(b"\x89PNG"))

    def test_qr_refuse_a_un_usager_interne_hors_du_module(self):
        """Sans cette assertion, le test précédent ne prouverait rien : il
        passerait aussi si la route était ouverte à tout usager connecté."""
        self._connecter(avec_groupe=False)
        response = self.url_open("/l/page-publique/qr.png", allow_redirects=False)
        self.assertNotEqual(response.status_code, 200)

    def test_qr_a_la_marque_reste_lisible(self):
        """Le logo masque des modules : le décodage est la seule preuve.

        Un test qui vérifierait seulement « un PNG est produit » ne
        discriminerait rien — un QR illisible est un PNG parfaitement valide.
        """
        try:
            from pyzbar.pyzbar import decode  # noqa: PLC0415
            from PIL import Image  # noqa: PLC0415
        except ImportError:
            self.skipTest("pyzbar absent : le décodage du QR n'est pas vérifiable ici")
        import io
        payload = self.page._qr_png(branded=True)
        decoded = decode(Image.open(io.BytesIO(payload)))
        self.assertTrue(decoded, "le QR à la marque doit rester décodable")
        self.assertEqual(decoded[0].data.decode(), self.page.public_url)

    def test_la_route_du_qr_est_declaree_reservee_aux_usagers(self):
        """Contrôle de contrat, et non de comportement.

        Mesuré le 2026-08-30 : basculer la route en `auth="public"` ne change
        RIEN d'observable, parce que l'usager public n'a de toute façon aucun
        droit de lecture sur le modèle et reçoit une erreur avant qu'aucune
        image ne soit composée. La protection tient donc à l'ACL, et
        `auth="user"` est une seconde barrière. Une seconde barrière qu'aucun
        test ne regarde finit par tomber sans bruit : celui-ci la regarde.
        """
        for rule in self.env["ir.http"].routing_map().iter_rules():
            if str(rule.rule) == "/l/<string:slug>/qr.png":
                routing = rule.endpoint.routing
                self.assertEqual(routing.get("auth"), "user")
                break
        else:
            self.fail("la route du QR est absente de la table de routage")
