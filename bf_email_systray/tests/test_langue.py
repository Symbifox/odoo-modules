"""Traductions du bouton de la barre servies au client web.

La source du module est en anglais ; le français vit dans `i18n/fr_CA.po`. Le
rendu se vérifie au navigateur ; ici, on vérifie ce que le navigateur reçoit :
le dictionnaire de la route `/web/webclient/translations`, que le client verse
module après module dans un seul dictionnaire (le dernier module qui porte un
msgid gagne). Ce que ces essais prouvent :

* chaque `_t(...)` du code (dont le titre du bouton, qui dit s'il ouvre un
  panneau ou la pleine page) est extrait par Odoo ;
* chaque libellé du code et des gabarits reçoit en fr_CA la traduction de ce
  module, et non celle d'un autre module qui partagerait le même msgid court
  (« Inbox », « Resize »…).
"""

import glob
import json
import os
import re

from babel.messages import extract

from odoo.modules.module import get_module_path
from odoo.tests import HttpCase, tagged

ATTENDU_JS = {
    "How to open the inbox": "Comment ouvrir la boîte",
    "Open in a panel": "Ouvrir en panneau",
    "Open full page": "Ouvrir en pleine page",
    "Open the list view": "Ouvrir la vue (liste)",
    "Always in a panel": "Toujours en panneau",
    "Always full page": "Toujours en pleine page",
    "Inbox": "Boîte de réception",
    "Emails:": "Courriels :",
    "Reset to the default size": "Revenir à la taille par défaut",
    "Close the panel (Esc)": "Fermer (Échap)",
    "Resize": "Redimensionner",
}

# Un `_t("…")` littéral dans le code (guillemets doubles, échappements JSON).
_T_LITTERAL = re.compile(r'\b_t\(\s*"((?:[^"\\]|\\.)*)"')


def sources_du_module():
    racine = get_module_path("bf_email_systray")
    return (sorted(glob.glob(os.path.join(racine, "static/src/js/*.js"))),
            sorted(glob.glob(os.path.join(racine, "static/src/xml/*.xml"))))


def extraits_par_odoo(js, xml):
    """Mêmes extracteurs et mots-clés que l'export des termes d'Odoo."""
    ids = set()
    for chemin in js:
        with open(chemin, "rb") as f:
            ids.update(m[1] for m in extract.extract("javascript", f, keywords={"_t": None}))
    for chemin in xml:
        with open(chemin, "rb") as f:
            ids.update(m[1] for m in extract.extract(
                "odoo.tools.translate:babel_extract_qweb", f, keywords={}))
    return ids


@tagged("post_install", "-at_install")
class TestTraductionsDuClientWeb(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("fr_CA")

    def _route(self):
        rep = self.url_open("/web/webclient/translations/essai?lang=fr_CA", timeout=60)
        self.assertEqual(rep.status_code, 200, rep.text[:300])
        corps = rep.json()
        self.assertEqual(corps["lang"], "fr_CA")
        return corps["modules"]

    def _fusion(self, modules):
        termes, porteurs = {}, {}
        for module, bloc in modules.items():
            for message in bloc.get("messages", []):
                termes[message["id"]] = message["string"]
                porteurs.setdefault(message["id"], []).append((module, message["string"]))
        return termes, porteurs

    def test_le_module_sert_son_francais(self):
        propres = {m["id"]: m["string"]
                   for m in self._route().get("bf_email_systray", {}).get("messages", [])}
        manquants = {k: v for k, v in ATTENDU_JS.items() if propres.get(k) != v}
        self.assertFalse(manquants, "Absents ou autres dans le bloc du module : %r" % manquants)

    def test_le_client_affiche_notre_francais(self):
        termes, porteurs = self._fusion(self._route())
        ecarts = {k: (termes.get(k), porteurs.get(k)) for k, v in ATTENDU_JS.items()
                  if termes.get(k) != v}
        self.assertFalse(ecarts, "Écrasés par un autre module dans le client : %r" % ecarts)

    def test_chaque_t_du_code_est_extrait(self):
        js, xml = sources_du_module()
        extraits = extraits_par_odoo(js, xml)
        litteraux = set()
        for chemin in js:
            with open(chemin, encoding="utf-8") as f:
                litteraux.update(json.loads('"%s"' % m) for m in _T_LITTERAL.findall(f.read()))
        self.assertTrue(litteraux)
        self.assertFalse(litteraux - extraits,
                         "`_t` du code qu'Odoo n'extrait pas : %r" % (litteraux - extraits))

    def test_chaque_libelle_du_code_recoit_notre_francais(self):
        """Titre du bouton (bf_email_systray.js) et gabarits : chaque msgid que
        le CODE emploie sort du client avec la traduction de NOTRE bloc ; un
        « Close (Esc) » repris recevrait celle de `web` (« Fermer (Esc) »)."""
        modules = self._route()
        termes, porteurs = self._fusion(modules)
        propres = {m["id"]: m["string"]
                   for m in modules.get("bf_email_systray", {}).get("messages", [])}
        js, xml = sources_du_module()
        ecarts = {}
        for msgid in extraits_par_odoo(js, xml):
            if not propres.get(msgid) or termes.get(msgid) != propres[msgid]:
                ecarts[msgid] = (propres.get(msgid), termes.get(msgid), porteurs.get(msgid))
        self.assertFalse(ecarts, "Libellés du code sans notre français dans le client : %r" % ecarts)
