from urllib.parse import urlsplit

from odoo import api, models

CLE_HOTE_WOPI = "cool_wopi_host_url"


class IrConfigParameter(models.Model):
    _inherit = "ir.config_parameter"

    @api.model
    def get_param(self, key, default=False):
        """Bâtir l'adresse WOPI sur l'hôte que le navigateur utilise VRAIMENT.

        🔴 Collabora calcule son en-tête `frame-ancestors` à partir du seul hôte
        du `WOPISrc`. Mesuré : un `WOPISrc` sur `www.exemple.com` rend
        `frame-ancestors … www.exemple.com:*`, et rien d'autre. Si la personne
        navigue sur `exemple.com` sans le `www`, le navigateur refuse le cadre
        et affiche « refused to connect », alors que tout le reste fonctionne :
        la page rend 200 et Collabora appelle bien `CheckFileInfo`.

        Le même Odoo répond souvent sur les deux formes du domaine. Le réglage,
        lui, n'en porte qu'une. On rend donc celle de la requête en cours.

        ⚠️ **Le seul écart toléré est la présence ou l'absence de `www.`.** Un
        hôte quelconque venu d'un en-tête `Host` forgé ne passe pas : il ferait
        pointer le `WOPISrc` ailleurs. Le schéma reste celui du réglage, jamais
        celui de la requête.
        """
        valeur = super().get_param(key, default)
        if key != CLE_HOTE_WOPI or not valeur or not isinstance(valeur, str):
            return valeur
        return self._bf_hote_wopi_de_la_requete(valeur)

    @api.model
    def _bf_hote_wopi_de_la_requete(self, configure):
        try:
            from odoo.http import request
            hote = urlsplit(request.httprequest.host_url).netloc
        except Exception:
            return configure
        if not hote:
            return configure
        reglage = urlsplit(configure)
        if not reglage.netloc or hote == reglage.netloc:
            return configure

        def sans_www(netloc):
            return netloc[4:] if netloc.startswith("www.") else netloc

        if sans_www(hote) != sans_www(reglage.netloc):
            return configure
        return "%s://%s" % (reglage.scheme or "https", hote)
