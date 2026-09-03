from unittest.mock import patch as remplacer

from odoo.tests.common import TransactionCase, tagged

from odoo.addons.bf_collabora_online.utils import cache_decouverte


@tagged("post_install", "-at_install")
class TestCacheDecouverte(TransactionCase):

    def setUp(self):
        super().setUp()
        cache_decouverte.vider()
        self.addCleanup(cache_decouverte.vider)
        self.appels = []

        def faux_original(server, mime_type, disable_verify_cert=False):
            self.appels.append((server, mime_type))
            return "https://exemple.invalide/browser/abc/cool.html?"

        self.remplacement = remplacer.object(
            cache_decouverte, "_original", faux_original)
        self.remplacement.start()
        self.addCleanup(self.remplacement.stop)

    def _regler_ttl(self, valeur):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_collabora.decouverte_ttl", valeur)

    def test_second_appel_ne_retelecharge_pas(self):
        with remplacer.object(cache_decouverte, "_duree_de_vie", lambda: 900):
            cache_decouverte.collabora_url("https://cool.exemple", "application/pdf")
            cache_decouverte.collabora_url("https://cool.exemple", "application/pdf")
        self.assertEqual(len(self.appels), 1,
                         "la découverte doit être gardée entre deux ouvertures")

    def test_type_mime_different_est_un_appel_different(self):
        with remplacer.object(cache_decouverte, "_duree_de_vie", lambda: 900):
            cache_decouverte.collabora_url("https://cool.exemple", "application/pdf")
            cache_decouverte.collabora_url("https://cool.exemple", "text/csv")
        self.assertEqual(len(self.appels), 2)

    def test_serveur_different_est_un_appel_different(self):
        """Un locataire ne doit pas hériter de l'adresse d'un autre."""
        with remplacer.object(cache_decouverte, "_duree_de_vie", lambda: 900):
            cache_decouverte.collabora_url("https://cool.un", "application/pdf")
            cache_decouverte.collabora_url("https://cool.deux", "application/pdf")
        self.assertEqual(len(self.appels), 2)

    def test_ttl_a_zero_retelecharge_toujours(self):
        with remplacer.object(cache_decouverte, "_duree_de_vie", lambda: 0):
            cache_decouverte.collabora_url("https://cool.exemple", "application/pdf")
            cache_decouverte.collabora_url("https://cool.exemple", "application/pdf")
        self.assertEqual(len(self.appels), 2,
                         "0 doit rendre le comportement de l'amont")

    def test_peremption(self):
        with remplacer.object(cache_decouverte, "_duree_de_vie", lambda: 900):
            cache_decouverte.collabora_url("https://cool.exemple", "application/pdf")
        # Reculer la péremption plutôt que d'attendre : ce qu'on éprouve est la
        # comparaison, pas l'horloge.
        for cle in list(cache_decouverte._cache):
            peremption, url = cache_decouverte._cache[cle]
            cache_decouverte._cache[cle] = (peremption - 10000, url)
        with remplacer.object(cache_decouverte, "_duree_de_vie", lambda: 900):
            cache_decouverte.collabora_url("https://cool.exemple", "application/pdf")
        self.assertEqual(len(self.appels), 2)

    def test_vidage_manuel(self):
        with remplacer.object(cache_decouverte, "_duree_de_vie", lambda: 900):
            cache_decouverte.collabora_url("https://cool.exemple", "application/pdf")
            self.assertEqual(self.env["bf.collabora.helper"].vider_cache_decouverte(), 1)
            cache_decouverte.collabora_url("https://cool.exemple", "application/pdf")
        self.assertEqual(len(self.appels), 2,
                         "après un vidage, la découverte doit repartir du serveur")

    def test_le_reglage_est_lu(self):
        """Le TTL vient bien du paramètre, pas seulement du repli du code."""
        self._regler_ttl("0")
        # Hors requête HTTP, la lecture du paramètre n'est pas possible : le
        # repli s'applique. C'est le comportement voulu pour un cron.
        self.assertEqual(cache_decouverte._duree_de_vie(),
                         cache_decouverte.TTL_DEFAUT)

    def test_l_amont_est_bien_remplace(self):
        from odoo.addons.collabora_odoo.utils import discover
        self.assertIs(discover.collabora_url, cache_decouverte.collabora_url,
                      "sans ce remplacement, le cache ne sert jamais")
