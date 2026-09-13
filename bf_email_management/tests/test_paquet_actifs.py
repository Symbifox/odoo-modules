"""Le paquet d'actifs se bâtit, et c'est le NÔTRE qui y est.

⚠️ Pourquoi ce contrôle existe. Une virgule de trop dans un fichier JS ou un
gabarit OWL malformé ne fait échouer aucun test Python : le serveur démarre,
les tests passent, et c'est le navigateur qui tombe, chez l'usager. La maison
s'est déjà fait prendre.

⚠️ Et pourquoi il vit dans un TEST et non dans un contrôle au shell. Sur le
banc, `odoo shell` résout `odoo.addons.__path__` depuis `/etc/odoo/odoo.conf`
et NON depuis le `-c` qu'on lui passe : il charge donc une autre copie du
module que celle qu'on vient d'écrire, et bâtit un paquet qui ne prouve rien.
Le lanceur de tests, lui, résout le bon arbre.
"""
from odoo.tests import tagged

from .common import MobileApiCase


@tagged("post_install", "-at_install")
class TestPaquetActifs(MobileApiCase):

    def _bundle(self):
        return self.env["ir.qweb"]._get_asset_bundle("web.assets_backend")

    def test_le_javascript_se_compile(self):
        self._bundle().js()

    def test_les_gabarits_owl_se_batissent(self):
        self._bundle().xml()

    def test_nos_fichiers_sont_dans_le_paquet(self):
        urls = {str(f.get("url")) for f in self._bundle().files}
        for attendu in (
            "/bf_email_management/static/src/js/bf_email_inbox.js",
            "/bf_email_management/static/src/xml/bf_email_inbox.xml",
            "/bf_email_management/static/src/js/bf_email_preview_list.js",
        ):
            self.assertIn(attendu, urls)

    def test_c_est_bien_la_version_de_ce_lot(self):
        """Le paquet bâti contient les gestes de, pas une copie plus
        ancienne trouvée ailleurs dans le chemin d'addons.

        ⚠️ `bundle.xml()` ne rend pas une chaîne mais une structure d'éléments
        lxml : chercher un marqueur dans `str()` du tout passe sur des
        `<Element t at 0x…>` et ne prouve rien. On sérialise les gabarits qui
        viennent de chez nous.
        """
        from lxml import etree
        morceaux = []
        for bloc in self._bundle().xml():
            for gabarit in bloc.get("templates", []):
                element, url = gabarit[0], gabarit[1]
                if "bf_email_management" in str(url):
                    morceaux.append(
                        etree.tostring(element, encoding="unicode"))
        self.assertTrue(morceaux, "aucun gabarit bf_email dans le paquet")
        texte = "\n".join(morceaux)
        for marqueur in ("showShortcuts", "loadRemoteImages", "toggleMute",
                         "unsubscribe", "toggleGrouped"):
            self.assertIn(marqueur, texte, marqueur)
