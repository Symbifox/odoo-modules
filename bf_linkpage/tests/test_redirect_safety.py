"""Le filtre de schéma sur l'adresse RÉSOLUE.

Ce que le QA du 2026-08-30 a d'abord cru trouver, et ce qu'il a réellement
trouvé — les deux méritent d'être écrits, parce que la différence est la leçon.

CRU : une redirection ouverte. `//exemple.invalide/x` posé dans la fiche d'un
contact ressortait bien dans l'en-tête `Location` de `/l/<slug>/go/<id>`.

TROUVÉ, après vérification : `res.partner.website` NORMALISE à l'écriture — le
protocole-relatif devient `http://…`, et même `  javascript:alert(1)` devient
`http://  javascript:alert(1)`, une adresse http inerte. Aucune des six sources
d'aujourd'hui ne peut donc faire sortir un schéma exécutable, et rediriger vers
un site externe est la fonction même d'une page de liens.

CE QUI RESTE VRAI : la contrainte d'écriture ne regarde que le champ `url`,
donc elle ne couvre QUE la source « adresse saisie ». Une source ajoutée plus
tard qui lirait un champ non normalisé n'aurait aucun garde-fou. `_safe_url`
est ce garde-fou, et il se teste là où il vit — sur la fonction — puisque
aucune source actuelle ne peut lui présenter une valeur hostile.
"""

from odoo.tests import HttpCase, TransactionCase, tagged

from ..models.linkpage_link import _safe_url

SCHEMAS_REFUSES = [
    ("javascript:alert(1)", "schéma exécutable"),
    ("JaVaScRiPt:alert(1)", "schéma exécutable, casse mélangée"),
    ("data:text/html,<script>1</script>", "document en ligne"),
    ("vbscript:msgbox(1)", "schéma exécutable hérité"),
    ("file:///etc/passwd", "schéma local"),
    ("//exemple.invalide/x", "protocole-relatif brut, sans normalisation amont"),
    ("  javascript:alert(1)", "schéma exécutable précédé d'espaces"),
]

SCHEMAS_ADMIS = [
    "https://exemple.invalide/page",
    "http://exemple.invalide/page",
    "mailto:quelquun@exemple.invalide",
    "tel:+15145550142",
    "sms:+15145550142",
]


@tagged("bf_linkpage", "post_install", "-at_install")
class TestSafeUrl(TransactionCase):

    def test_les_schemas_dangereux_sont_refuses(self):
        for value, pourquoi in SCHEMAS_REFUSES:
            self.assertFalse(_safe_url(value), "%s : %r" % (pourquoi, value))

    def test_les_schemas_legitimes_passent(self):
        """Sans cette assertion, un filtre qui refuse TOUT passerait le test
        précédent sans rien protéger."""
        for value in SCHEMAS_ADMIS:
            self.assertEqual(_safe_url(value), value)

    def test_une_adresse_vide_ne_passe_pas(self):
        self.assertFalse(_safe_url(False))
        self.assertFalse(_safe_url(""))


@tagged("bf_linkpage", "post_install", "-at_install")
class TestRedirectSafety(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({"name": "Redirection"})
        cls.page = cls.env["bf.linkpage"].create({
            "name": "Redirection", "slug": "redirection", "kind": "owner",
            "partner_id": cls.partner.id, "state": "published"})

    def _lien_site(self):
        return self.env["bf.linkpage.link"].create({
            "page_id": self.page.id, "name": "Site",
            "source_code": "partner_website"})

    def test_une_url_externe_legitime_redirige(self):
        self.partner.website = "https://exemple.invalide/vrai"
        link = self._lien_site()
        self.env.flush_all()
        response = self.url_open("/l/redirection/go/%s" % link.id,
                                 allow_redirects=False)
        self.assertIn(response.status_code, (302, 303))
        self.assertEqual(response.headers.get("Location"),
                         "https://exemple.invalide/vrai")

    def test_le_protocole_relatif_est_normalise_en_amont(self):
        """Fige le comportement sur lequel repose la conclusion ci-dessus.

        Si Odoo cessait un jour de normaliser `website`, ce test rougirait et
        signalerait que le filtre devient la seule protection en ligne.
        """
        self.partner.website = "//exemple.invalide/x"
        self.assertEqual(self.partner.website, "http://exemple.invalide/x")
        self.assertEqual(self._lien_site().resolved_url,
                         "http://exemple.invalide/x")

    def test_un_lien_dont_l_adresse_ne_passe_pas_le_filtre_rend_404(self):
        """La preuve en bout de chaîne : un lien dont l'adresse résolue est
        écartée disparaît, donc /go/ ne le trouve plus et ne pose AUCUN
        en-tête Location."""
        link = self._lien_site()
        self.partner.website = "https://exemple.invalide/ok"
        link.invalidate_recordset()
        self.env.flush_all()
        self.assertTrue(link.resolved_url)
        # On force une adresse hostile là où aucune source ne peut en produire.
        self.env.cr.execute(
            "UPDATE res_partner SET website = %s WHERE id = %s",
            ("javascript:alert(1)", self.partner.id))
        self.env.invalidate_all()
        self.assertFalse(link.resolved_url,
                         "le filtre doit écarter le schéma exécutable")
        response = self.url_open("/l/redirection/go/%s" % link.id,
                                 allow_redirects=False)
        self.assertEqual(response.status_code, 404)
        self.assertIsNone(response.headers.get("Location"))
