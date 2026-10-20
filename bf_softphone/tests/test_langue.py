"""Langue des refus, des libellés et des traductions du client web.

La source du module est en anglais ; le français vit dans `i18n/fr_CA.po`.
Ce que ces essais prouvent :

* les refus (accès, touche invalide, numéro non composable, lien avec le PBX,
  étranglement) sont dans la langue de l'usager qui agit ;
* les libellés de la composition par le PBX (poste qui sonne, nom affiché sur
  le téléphone de l'usager) suivent la langue de l'USAGER, même quand la
  requête n'en porte aucune ou en porte une autre ;
* l'activité « rappeler » d'un appel manqué est dans la langue de l'usager ;
* chaque `_t(...)` du code est extrait par Odoo, et chaque libellé du client
  web reçoit en fr_CA la traduction de ce module, et non celle d'un autre
  module qui partagerait le même msgid.

Les essais épinglent fr_CA et en_CA, qu'ils activent, et créent leurs usagers
avec une langue explicite : la langue par défaut de la base n'est pas connue
d'avance. Aucun PBX n'est joint : `originate` est remplacé par un espion.
Tous les numéros sont fictifs (555-01xx).
"""

import glob
import json
import os
import re
from unittest.mock import patch

from babel.messages import extract

from odoo.exceptions import AccessError, UserError
from odoo.modules.module import get_module_path
from odoo.tests import HttpCase, TransactionCase, tagged

from ..models import res_users as res_users_module

# Numéro fictif (555-01xx), qu'aucun contact ne porte : l'afficheur retombe sur
# le libellé générique.
NUMERO = "418 555-0176"


@tagged("post_install", "-at_install")
class TestLangueSoftphone(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for code in ("fr_CA", "en_CA"):
            cls.env["res.lang"]._activate_lang(code)
        cls.env["ir.module.module"]._load_module_terms(
            ["bf_softphone"], ["fr_CA"], overwrite=True)
        groupe = cls.env.ref("bf_softphone.group_softphone_user").id

        def poste(login, lang, extension):
            usager = cls.env["res.users"].create({
                "name": "Poste %s" % login, "login": login, "lang": lang,
                "groups_id": [(4, groupe)],
            })
            usager.sudo().write({"sip_extension": extension,
                                 "sip_callback_number": "514 555 0102"})
            return usager

        cls.fr = poste("essai_fr", "fr_CA", "1071")
        cls.en = poste("essai_en", "en_CA", "1072")
        cls.dehors = {
            lang: cls.env["res.users"].create({
                "name": "Sans téléphone %s" % lang, "login": "essai_dehors_%s" % lang,
                "lang": lang, "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
            })
            for lang in ("fr_CA", "en_CA")
        }
        ICP = cls.env["ir.config_parameter"].sudo()
        ICP.set_param("bf_softphone.trunk", "trunk-test")
        ICP.set_param("bf_softphone.originate_context", "from-internal")
        ICP.set_param("bf_softphone.outbound_cid", "514 555-0101")

    def setUp(self):
        super().setUp()
        res_users_module._CALL_HITS.clear()
        res_users_module._SEARCH_HITS.clear()

    def _users(self, usager, lang):
        """Comme un appel RPC : l'usager, et la langue que porte la requête."""
        return self.env["res.users"].with_user(usager).with_context(lang=lang)

    def _espion(self):
        return patch.object(
            type(self.env["bf.softphone.ami"]), "originate", return_value={"ok": True})

    # ── Refus (règle 1) ───────────────────────────────────────────────
    def test_acces_refuse_dans_la_langue_de_l_usager(self):
        """res_users.py:199 (softphone_search_contacts)."""
        with self.assertRaises(AccessError) as en:
            self._users(self.dehors["en_CA"], "en_CA").softphone_search_contacts("Jean")
        self.assertEqual(str(en.exception), "Phone access not allowed.")
        with self.assertRaises(AccessError) as fr:
            self._users(self.dehors["fr_CA"], "fr_CA").softphone_search_contacts("Jean")
        self.assertEqual(str(fr.exception), "Accès au téléphone non autorisé.")

    def test_acces_ice_refuse_dans_la_langue_de_l_usager(self):
        """softphone_ice.py:41."""
        Ice = self.env["bf.softphone.ice"]
        with self.assertRaises(AccessError) as en:
            Ice.with_user(self.dehors["en_CA"]).with_context(lang="en_CA").get_ice_servers()
        self.assertEqual(str(en.exception), "Phone access not allowed.")
        with self.assertRaises(AccessError) as fr:
            Ice.with_user(self.dehors["fr_CA"]).with_context(lang="fr_CA").get_ice_servers()
        self.assertEqual(str(fr.exception), "Accès au téléphone non autorisé.")

    def test_touche_invalide_dans_la_langue_de_l_usager(self):
        """res_users.py:436 (softphone_send_dtmf)."""
        with self.assertRaises(UserError) as en:
            self._users(self.en, "en_CA").softphone_send_dtmf("zz")
        self.assertEqual(str(en.exception), "No valid key.")
        with self.assertRaises(UserError) as fr:
            self._users(self.fr, "fr_CA").softphone_send_dtmf("zz")
        self.assertEqual(str(fr.exception), "Aucune touche valide.")

    def test_numero_non_composable_dans_la_langue_de_l_usager(self):
        """res_users.py:317 (softphone_originate)."""
        with self.assertRaises(UserError) as en:
            self._users(self.en, "en_CA").softphone_originate("+33123456789")
        self.assertIn("only ten-digit North American numbers", str(en.exception))
        with self.assertRaises(UserError) as fr:
            self._users(self.fr, "fr_CA").softphone_originate("+33123456789")
        self.assertIn("nord-américains à dix chiffres", str(fr.exception))

    def test_mode_de_sonnerie_inconnu_dans_la_langue_de_l_usager(self):
        """res_users.py:325."""
        with self.assertRaises(UserError) as en:
            self._users(self.en, "en_CA").softphone_originate(NUMERO, ring="pigeon")
        self.assertEqual(str(en.exception), "Unknown ring mode.")

    def test_lien_ami_absent_dans_la_langue_de_l_usager(self):
        """ami.py:133 (_connect_and_login)."""
        ICP = self.env["ir.config_parameter"].sudo()
        ICP.set_param("bf_softphone.ami_host", "")
        ICP.set_param("bf_softphone.ami_user", "")
        Ami = self.env["bf.softphone.ami"]
        with self.assertRaises(UserError) as en:
            Ami.with_context(lang="en_CA")._connect_and_login()
        self.assertEqual(str(en.exception), "The PBX link is not configured.")
        with self.assertRaises(UserError) as fr:
            Ami.with_context(lang="fr_CA")._connect_and_login()
        self.assertEqual(str(fr.exception), "Le lien avec le PBX n'est pas configuré.")

    def test_etranglement_de_la_recherche_dans_la_langue_de_l_usager(self):
        """res_users.py:538 : sans tiret cadratin en anglais."""
        Users = self._users(self.en, "en_CA")
        # Compteur rempli d'avance : la recherche elle-même exige le droit de
        # lire les contacts, que ce poste d'essai n'a pas forcément.
        maintenant = Users._softphone_now()
        res_users_module._SEARCH_HITS[self.en.id] = [maintenant] * res_users_module._SEARCH_MAX
        with self.assertRaises(UserError) as en:
            Users._softphone_throttle()
        self.assertEqual(str(en.exception), "Too many searches, try again in a moment.")

    # ── Composition par le PBX : langue de l'usager (point 4) ─────────
    def test_poste_qui_sonne_dans_la_langue_de_l_usager(self):
        """res_users.py:337, 343 : la route mobile peut arriver sans langue (ou
        avec une autre) ; `ring_label` suit la langue de l'usager."""
        with self._espion():
            fr = self._users(self.fr, "en_CA").softphone_originate(NUMERO, ring="extension")
            en = self._users(self.en, "fr_CA").softphone_originate(NUMERO, ring="extension")
        self.assertEqual(fr["ring_label"], "poste 1071")
        self.assertEqual(en["ring_label"], "extension 1072")

    def test_afficheur_du_poste_dans_la_langue_de_l_usager(self):
        """res_users.py:350 : nom d'afficheur générique quand le correspondant
        est inconnu."""
        with self._espion() as espion:
            self._users(self.fr, "en_CA").softphone_originate(NUMERO, ring="extension")
            afficheur_fr = espion.call_args.kwargs["callerid"]
            self._users(self.en, "fr_CA").softphone_originate(NUMERO, ring="extension")
            afficheur_en = espion.call_args.kwargs["callerid"]
        self.assertTrue(afficheur_fr.startswith('"Appel" <'), afficheur_fr)
        self.assertTrue(afficheur_en.startswith('"Outgoing call" <'), afficheur_en)

    def test_afficheur_du_trunk_dans_la_langue_de_l_usager(self):
        """res_users.py:369 : la jambe qui sort par le trunk porte le correspondant
        dans le NOM, et ce nom est lu par l'usager sur son téléphone."""
        with self._espion() as espion:
            self._users(self.fr, "en_CA").softphone_originate(NUMERO, ring="callback")
            afficheur_fr = espion.call_args.kwargs["callerid"]
            self._users(self.en, "fr_CA").softphone_originate(NUMERO, ring="callback")
            afficheur_en = espion.call_args.kwargs["callerid"]
        self.assertEqual(afficheur_fr, '"Appel 418 555-0176" <15145550101>')
        self.assertEqual(afficheur_en, '"Call 418 555-0176" <15145550101>')

    # ── Activité d'appel manqué (règles 1-2) ──────────────────────────
    def test_activite_rappeler_dans_la_langue_de_l_usager(self):
        """res_users.py:521 : le résumé « Rappeler %s » était en dur."""
        for usager, lang, attendu in ((self.fr, "fr_CA", "Rappeler Contact d'essai %s"),
                                      (self.en, "en_CA", "Call back Contact d'essai %s")):
            partenaire = self.env["res.partner"].create({"name": "Contact d'essai %s" % lang})
            Users = self._users(usager, lang)
            call_id = Users.softphone_log_call(NUMERO, "incoming", duration=5)
            appel = self.env["call.archive.call"].sudo().browse(call_id)
            appel.thread_id.sudo().partner_id = partenaire
            Users._softphone_missed_activity(appel)
            activite = self.env["mail.activity"].sudo().search([
                ("res_model", "=", "res.partner"), ("res_id", "=", partenaire.id)])
            self.assertEqual(activite.summary, attendu % lang)


# Ce que le client web doit afficher en fr_CA pour chaque msgid JS du module.
ATTENDU_JS = {
    "Incoming call": "Appel entrant",
    "from %s": "de %s",
    "Call to %s failed.": "Appel vers %s non abouti.",
    "Call to %(number)s failed (%(cause)s).": "Appel vers %(number)s non abouti (%(cause)s).",
    "Phone connection failed: %s": "Échec de connexion du téléphone : %s",
    "authentication": "authentification",
    'Number cannot be dialed: only North American numbers are supported ("%s").':
        "Numéro non composable : seuls les numéros nord-américains le sont (« %s »).",
    "Phone": "Téléphone",
    "Mute microphone": "Couper le micro",
    "Unmute microphone": "Réactiver le micro",
    "Sent: %s": "Envoyé : %s",
    "Unknown": "Inconnu",
    # Gabarit OWL.
    "Online": "En ligne",
    "Connecting…": "Connexion…",
    "Failed": "Échec",
    "Offline": "Hors ligne",
    "Incoming call…": "Appel entrant…",
    "Answer": "Répondre",
    "Decline": "Refuser",
    "Keypad (phone menus)": "Clavier (menus téléphoniques)",
    "Hang up": "Raccrocher",
    "Number or name…": "Numéro ou nom…",
    "Call": "Appeler",
    "Recent calls": "Récents",
}

# Un `_t("…")` littéral dans le code (guillemets doubles, échappements JSON).
_T_LITTERAL = re.compile(r'\b_t\(\s*"((?:[^"\\]|\\.)*)"')


def termes_du_client(case, lang):
    """Le dictionnaire tel que le client web le construit : la route rend un
    bloc par module, et `localization_service.js` les verse l'un après l'autre
    dans un seul dictionnaire, le dernier module qui porte un msgid gagnant."""
    rep = case.url_open("/web/webclient/translations/essai?lang=%s" % lang, timeout=60)
    case.assertEqual(rep.status_code, 200, rep.text[:300])
    corps = rep.json()
    case.assertEqual(corps["lang"], lang)
    termes, porteurs = {}, {}
    for module, bloc in corps["modules"].items():
        for message in bloc.get("messages", []):
            termes[message["id"]] = message["string"]
            porteurs.setdefault(message["id"], []).append((module, message["string"]))
    return corps["modules"], termes, porteurs


def sources_du_module():
    """Les fichiers JS et gabarits du module, comme l'export d'Odoo les parcourt
    (bibliothèque tierce exclue)."""
    racine = get_module_path("bf_softphone")
    js = sorted(glob.glob(os.path.join(racine, "static/src/js/*.js")))
    xml = sorted(glob.glob(os.path.join(racine, "static/src/xml/*.xml")))
    return js, xml


def extraits_par_odoo(js, xml):
    """Les msgids qu'Odoo extrait réellement : mêmes extracteurs et mots-clés que
    `TranslationModuleReader._export_translatable_resources`."""
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
    """Les msgids JS courts (« Phone », « Mute », « Call »…) sont partagés par
    tous les modules dans un seul dictionnaire côté client : vérifier que le nôtre
    y reçoit bien le français voulu, et non celui d'un autre module.

    Le catalogue est lu dans le .po, pas dans le code : les deux premiers essais
    lisent donc aussi le CODE, pour qu'un `_t` changé ou imbriqué tombe."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("fr_CA")

    def test_le_module_sert_son_francais(self):
        modules, _termes, _porteurs = termes_du_client(self, "fr_CA")
        propres = {m["id"]: m["string"]
                   for m in modules.get("bf_softphone", {}).get("messages", [])}
        manquants = {k: v for k, v in ATTENDU_JS.items() if propres.get(k) != v}
        self.assertFalse(manquants, "Absents ou autres dans le bloc bf_softphone : %r" % manquants)

    def test_le_client_affiche_notre_francais(self):
        _modules, termes, porteurs = termes_du_client(self, "fr_CA")
        ecarts = {k: (termes.get(k), porteurs.get(k)) for k, v in ATTENDU_JS.items()
                  if termes.get(k) != v}
        self.assertFalse(ecarts, "Écrasés par un autre module dans le client : %r" % ecarts)

    def test_chaque_t_du_code_est_extrait(self):
        """softphone_service.js:260 : un `_t(...)` passé en argument d'un autre
        `_t(...)` n'est pas extrait ; le francophone lisait « authentication »."""
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
        """softphone_panel.js:59 et softphone.xml:95 : chaque msgid que le CODE
        emploie doit sortir du client avec la traduction de NOTRE bloc ; un
        « Mute » ou un « Recent » repris reçoit celle de `mail`."""
        modules, termes, porteurs = termes_du_client(self, "fr_CA")
        propres = {m["id"]: m["string"]
                   for m in modules.get("bf_softphone", {}).get("messages", [])}
        js, xml = sources_du_module()
        ecarts = {}
        for msgid in extraits_par_odoo(js, xml):
            if not propres.get(msgid) or termes.get(msgid) != propres[msgid]:
                ecarts[msgid] = (propres.get(msgid), termes.get(msgid), porteurs.get(msgid))
        self.assertFalse(ecarts, "Libellés du code sans notre français dans le client : %r" % ecarts)
