"""L'autoconfiguration du courrier servie par Odoo.

Les contrôles qui tranchent, et ce qu'ils empêchent :

* `test_le_type_mime_est_du_xml` : Thunderbird n'analyse la réponse que si son `Content-Type`
  commence par `text/xml`, `application/xml` ou `text/rdf` (`FetchHTTP.sys.mjs:176`). Servi en
  `text/html`, le fichier serait ignoré en silence et la cascade tomberait sur l'ISPDB : rien ne
  casse, rien ne marche, et il n'y a rien à voir dans un journal.
* `test_sans_parametre_rien_n_est_publie` et `test_un_domaine_non_nomme_ne_recoit_rien` : une
  configuration servie sur un domaine dont le courrier est ailleurs est pire que pas de
  configuration (un domaine peut servir son propre IMAP). Le module
  est livré inerte, et c'est le paramètre du locataire qui ouvre la porte, un domaine à la fois.
* `test_l_adresse_demandee_n_est_pas_recopiee` : le `?emailaddress=` du client ne doit jamais
  entrer dans un fichier XML servi sans authentification à qui le demande.
* `test_la_route_n_est_pas_avalee_par_le_site` : sans route déclarée, le site Odoo rend un 303
  vers `/en/…` sur ce chemin (constaté). Un client qui reçoit une
  redirection vers une page HTML repart les mains vides.
* `test_le_fichier_porte_ce_que_l_analyseur_de_thunderbird_exige` : son analyseur lève s'il
  manque un `<domain>`, et rejette le fichier entier si `<socketType>` sort des trois valeurs
  qu'il connaît.
* `test_une_fiche_de_fournisseur_de_travers_ne_sert_rien` : la table `FOURNISSEURS` est tenue
  pour la cascade de découverte, et qui y ajoute un fournisseur n'a aucune raison de
  savoir qu'une route publique la lit. Une fiche sans sa clé `smtp`, un `auth` sans traduction
  ou une prise inconnue doivent rendre 404 avec un mot au journal, jamais une trace de 500 sur
  tous les locataires à la fois.
* `test_un_nom_avec_du_balisage_reste_du_texte` et `test_un_nom_illisible_ne_sert_rien` : le nom
  affiché est la SEULE valeur libre qu'une opératrice fait entrer dans un document servi sans
  authentification. L'échappement de `lxml` le tient aujourd'hui ; ces deux essais l'empêchent
  de se perdre à la prochaine réécriture.
* `test_le_module_ne_livre_aucun_domaine` : toute la sûreté de la fonction sur les locataires
  qui ne doivent rien publier tient à ce qu'aucun domaine ne soit livré avec le module.
* `test_les_en_tetes_disent_quoi_garder` : une heure de cache public ferait survivre une
  configuration retirée, et un 404 gardé masquerait un domaine fraîchement publié.
"""

from unittest.mock import patch

from lxml import etree
from odoo.tests import HttpCase, TransactionCase, tagged

from ..controllers import autoconfig as ctrl

CHEMIN = "/.well-known/autoconfig/mail/config-v1.1.xml"
DOMAINE = "essai-bf.example"
JOURNAL = "odoo.addons.bf_email_management.controllers.autoconfig"


@tagged("post_install", "-at_install")
class TestAutoconfigServie(HttpCase):

    def setUp(self):
        super().setUp()
        self.param = self.env["ir.config_parameter"].sudo()
        self.param.set_param(ctrl.PARAM_NOM, "Exemple Inc.")

    def _publier(self, valeur):
        self.param.set_param(ctrl.PARAM_DOMAINES, valeur)

    def _demander(self, hote=DOMAINE, requete=""):
        return self.url_open(CHEMIN + requete, headers={"Host": hote}, allow_redirects=False)

    def _bloc(self, reponse):
        return etree.fromstring(reponse.content).find("emailProvider")

    # --- ce qui est publié, et pour qui -------------------------------------------------

    def test_sans_parametre_rien_n_est_publie(self):
        self._publier("")
        self.assertEqual(self._demander().status_code, 404)

    def test_un_domaine_non_nomme_ne_recoit_rien(self):
        self._publier(DOMAINE)
        self.assertEqual(self._demander(hote="voisin.example").status_code, 404)

    def test_un_domaine_nomme_recoit_sa_configuration(self):
        self._publier(DOMAINE)
        reponse = self._demander()
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(self._bloc(reponse).get("id"), DOMAINE)

    def test_le_port_et_le_www_sont_normalises(self):
        self._publier(DOMAINE)
        for hote in ("WWW." + DOMAINE.upper(), DOMAINE + ":8069", DOMAINE + "."):
            with self.subTest(hote=hote):
                reponse = self._demander(hote=hote)
                self.assertEqual(reponse.status_code, 200, hote)
                self.assertEqual(self._bloc(reponse).findtext("domain"), DOMAINE)

    def test_un_domaine_mal_forme_est_ignore_sans_tuer_les_autres(self):
        self._publier(f"pas-un-domaine, {DOMAINE}")
        # ⚠️ L'avertissement sort sur une requête dont l'hôte est VALIDE : un hôte invalide
        # fait court-circuiter la route avant toute lecture du paramètre (et c'est voulu, un
        # balayeur d'Internet ne doit rien coûter).
        with self.assertLogs(JOURNAL, "WARNING") as journal:
            self.assertEqual(self._demander().status_code, 200)
        self.assertIn("pas-un-domaine", " ".join(journal.output))
        self.assertEqual(self._demander(hote="pas-un-domaine").status_code, 404)

    def test_un_fournisseur_inconnu_ne_sert_rien(self):
        self._publier(f"{DOMAINE}=licorne")
        self.assertEqual(self._demander().status_code, 404)

    def test_le_fournisseur_se_nomme_domaine_par_domaine(self):
        self._publier(f"{DOMAINE}=google")
        bloc = self._bloc(self._demander())
        self.assertEqual(bloc.find("incomingServer").findtext("hostname"), "imap.gmail.com")
        # Google exige un mot de passe d'application : pour le client, c'est un mot de passe.
        self.assertEqual(bloc.find("incomingServer").findtext("authentication"),
                         "password-cleartext")

    def test_un_fournisseur_a_jeton_annonce_oauth2(self):
        self._publier(f"{DOMAINE}=microsoft")
        bloc = self._bloc(self._demander())
        self.assertEqual(bloc.find("incomingServer").findtext("authentication"), "OAuth2")
        self.assertEqual(bloc.find("outgoingServer").findtext("socketType"), "STARTTLS")

    # --- ce que le client exige --------------------------------------------------------

    def test_le_type_mime_est_du_xml(self):
        self._publier(DOMAINE)
        type_mime = self._demander().headers.get("Content-Type", "")
        self.assertTrue(
            type_mime.startswith(("text/xml", "application/xml", "text/rdf")),
            f"Thunderbird ignore une réponse qui n'annonce pas du XML : {type_mime!r}")

    def test_la_route_n_est_pas_avalee_par_le_site(self):
        installe = self.env["ir.module.module"].sudo().search_count(
            [("name", "=", "website"), ("state", "=", "installed")])
        if not installe:
            self.skipTest("sans le module website, cet essai ne mesurerait rien de plus")
        self._publier(DOMAINE)
        reponse = self._demander()
        self.assertEqual(reponse.status_code, 200)
        self.assertNotIn("Location", reponse.headers)

    def test_les_en_tetes_disent_quoi_garder(self):
        self._publier(DOMAINE)
        servi = self._demander()
        self.assertIn("max-age=600", servi.headers.get("Cache-Control", ""))
        self.assertEqual(servi.headers.get("Vary"), "Host")
        refus = self._demander(hote="voisin.example")
        self.assertEqual(refus.status_code, 404)
        self.assertIn("no-store", refus.headers.get("Cache-Control", ""))

    def test_une_methode_qui_ecrit_est_refusee(self):
        """⚠️ `data` doit être VRAI : `url_open` avec `data=b""` fait un GET, et l'essai
        passerait en mesurant la lecture (vu : 200 au lieu de 405)."""
        self._publier(DOMAINE)
        reponse = self.url_open(CHEMIN, data={"essai": "1"}, headers={"Host": DOMAINE},
                                allow_redirects=False)
        self.assertEqual(reponse.status_code, 405)

    def test_le_fichier_porte_ce_que_l_analyseur_de_thunderbird_exige(self):
        self._publier(DOMAINE)
        reponse = self._demander()
        racine = etree.fromstring(reponse.content)
        self.assertEqual(racine.tag, "clientConfig")
        self.assertEqual(racine.get("version"), "1.1")
        bloc = racine.find("emailProvider")
        self.assertEqual(bloc.findtext("domain"), DOMAINE)
        self.assertEqual(bloc.findtext("displayName"), "Exemple Inc.")
        attendu = {
            "incomingServer": ("imap", "imap.migadu.com", "993"),
            "outgoingServer": ("smtp", "smtp.migadu.com", "465"),
        }
        for balise, (type_, hote, port) in attendu.items():
            serveur = bloc.find(balise)
            self.assertIsNotNone(serveur, balise)
            self.assertEqual(serveur.get("type"), type_)
            self.assertEqual(serveur.findtext("hostname"), hote)
            self.assertEqual(serveur.findtext("port"), port)
            self.assertIn(serveur.findtext("socketType"), ctrl.SOCKETS)
            self.assertEqual(serveur.findtext("authentication"), "password-cleartext")
            self.assertEqual(serveur.findtext("username"), "%EMAILADDRESS%")

    def test_l_adresse_demandee_n_est_pas_recopiee(self):
        self._publier(DOMAINE)
        reponse = self._demander(requete="?emailaddress=%3Cscript%3Ealex%40ailleurs.example")
        corps = reponse.content.decode()
        self.assertIn("%EMAILADDRESS%", corps)
        for morceau in ("<script", "ailleurs.example", "alex@"):
            self.assertNotIn(morceau, corps)

    def test_rien_n_ouvre_de_session(self):
        self._publier(DOMAINE)
        reponse = self._demander()
        self.assertNotIn("Set-Cookie", reponse.headers)

    def test_une_fiche_de_fournisseur_de_travers_ne_sert_rien(self):
        """Mieux vaut ne rien servir qu'un fichier rejeté par le client, ou qu'une trace de 500."""
        self._publier(DOMAINE)
        bonne = ctrl.FOURNISSEURS["migadu"]
        # Chaque forme fautive dit AUSSI ce que le journal doit nommer : quand une garde est
        # doublée par le filet de la route, le message est le seul écart observable, et c'est
        # lui qui dit à l'opératrice quel champ est de travers.
        fautives = {
            "prise inconnue": (dict(bonne, imap=("imap.migadu.com", 993, "TLS-maison")),
                               "socketType"),
            "auth sans traduction": (dict(bonne, auth="kerberos-maison"), "auth"),
            "sans clé smtp": ({k: v for k, v in bonne.items() if k != "smtp"}, "smtp"),
            "sans clé auth": ({k: v for k, v in bonne.items() if k != "auth"}, "auth"),
            "port absent": (dict(bonne, imap=("imap.migadu.com", None, "SSL")), "port"),
            "port hors bornes": (dict(bonne, smtp=("smtp.migadu.com", 99999, "SSL")), "port"),
            "port booléen": (dict(bonne, smtp=("smtp.migadu.com", True, "SSL")), "port"),
            "hôte vide": (dict(bonne, smtp=("", 465, "SSL")), "smtp"),
            "hôte avec saut de ligne": (dict(bonne, smtp=("smtp.migadu.com\n", 465, "SSL")),
                                        "smtp"),
            "fiche tronquée": (dict(bonne, smtp=("smtp.migadu.com", 465)), ""),
            "fiche qui n'est pas un dict": ("imap.migadu.com:993", "dict"),
        }
        for quoi, (fiche, attendu) in fautives.items():
            with self.subTest(fiche=quoi), \
                    patch.dict(ctrl.FOURNISSEURS, {"migadu": fiche}), \
                    self.assertLogs(JOURNAL, "WARNING") as journal:
                self.assertEqual(self._demander().status_code, 404)
            if attendu:
                self.assertIn(attendu, " ".join(journal.output),
                              f"le journal ne nomme pas le champ fautif ({quoi})")

    # --- le nom affiché, seule valeur libre du fichier servi ---------------------------

    def test_le_nom_par_defaut_est_le_domaine(self):
        self.param.set_param(ctrl.PARAM_NOM, "")
        self._publier(DOMAINE)
        self.assertEqual(self._bloc(self._demander()).findtext("displayName"), DOMAINE)

    def test_un_nom_avec_du_balisage_reste_du_texte(self):
        mechant = '</displayName><script>alerte("x")</script><displayName>'
        self.param.set_param(ctrl.PARAM_NOM, mechant)
        self._publier(DOMAINE)
        racine = etree.fromstring(self._demander().content)
        self.assertEqual(racine.find("emailProvider").findtext("displayName"), mechant)
        self.assertEqual(racine.findall(".//script"), [])

    def test_un_nom_illisible_ne_sert_rien(self):
        """Un caractère que le XML n'accepte pas donne un refus, pas une trace de 500."""
        self.param.set_param(ctrl.PARAM_NOM, "Blue\x01Fox")
        self._publier(DOMAINE)
        with self.assertLogs(JOURNAL, "WARNING"):
            self.assertEqual(self._demander().status_code, 404)

    # --- ce que le module livre ---------------------------------------------------------

    def test_le_module_ne_livre_aucun_domaine(self):
        """Inerte à l'installation : c'est le locataire qui ouvre la porte, un domaine à la fois.

        ⚠️ Mesuré sur ce que le MODULE pose, pas sur la valeur vivante : lire le paramètre
        rendrait cet essai rouge dès qu'un locataire l'a posé, et la propriété voulue n'est pas
        celle-là.
        """
        poses = self.env["ir.model.data"].sudo().search([
            ("module", "=", "bf_email_management"), ("model", "=", "ir.config_parameter")])
        # ⚠️ `.exists()` : une ligne d'`ir.model.data` peut pointer un paramètre supprimé,
        # et `mapped` lève alors MissingError (vu sur une base réelle).
        cles = self.env["ir.config_parameter"].sudo().browse(
            poses.mapped("res_id")).exists().mapped("key")
        self.assertNotIn(ctrl.PARAM_DOMAINES, cles)
        self.assertNotIn(ctrl.PARAM_FOURNISSEUR, cles)

    def test_une_entree_www_au_parametre_sert_le_domaine(self):
        self._publier("www." + DOMAINE)
        self.assertEqual(self._demander().status_code, 200)
        self.assertEqual(self._demander(hote="www." + DOMAINE).status_code, 200)


class _Requete:
    """Le strict nécessaire de `request` pour éprouver les deux gardes une à une."""

    def __init__(self, hote, valeurs):
        self.httprequest = type("HR", (), {"host": hote})()
        self.params = {}
        self.env = {"ir.config_parameter": type(
            "Param", (), {"sudo": lambda soi: soi,
                          "get_param": lambda soi, cle, defaut="": valeurs.get(cle, defaut)})()}


@tagged("post_install", "-at_install")
class TestAutoconfigGardes(TransactionCase):
    """Les deux gardes se masquent l'une l'autre vues de l'extérieur.

    Un `Host` invalide n'est de toute façon pas une clé de la carte, et une entrée invalide du
    paramètre n'est de toute façon pas un `Host` valide : par HTTP, retirer l'une ou l'autre ne
    change aucune réponse (mesuré : deux mutants survivaient). Mesurées une à une,
    elles redeviennent tuables, et c'est la seule façon de tenir la sûreté là où elle est écrite.
    """

    def _hote(self, hote, domaines=""):
        with patch.object(ctrl, "request", _Requete(hote, {ctrl.PARAM_DOMAINES: domaines})):
            return ctrl._hote()

    def _carte(self, domaines):
        with patch.object(ctrl, "request", _Requete("x", {ctrl.PARAM_DOMAINES: domaines})):
            return ctrl._carte()

    def test_l_hote_refuse_ce_qui_n_est_pas_un_nom_d_hote(self):
        for hote in ("localhost", "ho\x00te.example", "ligne\r\nsuivante.example",
                     "[2001:db8::1]", "exemple .com", "-exemple.com", "sous_domaine.example",
                     "a" * 300 + ".example", ""):
            with self.subTest(hote=hote):
                self.assertEqual(self._hote(hote), "")

    def test_l_hote_normalise_ce_qui_en_est_un(self):
        for hote in ("WWW." + DOMAINE.upper(), DOMAINE + ":8069", DOMAINE + "."):
            with self.subTest(hote=hote):
                self.assertEqual(self._hote(hote), DOMAINE)

    def test_la_carte_refuse_une_entree_qui_n_est_pas_un_domaine(self):
        self.assertEqual(self._carte("pas-un-domaine, ..x, a.example"), {"a.example": "migadu"})

    def test_la_carte_refuse_un_fournisseur_inconnu(self):
        self.assertEqual(self._carte("a.example=licorne"), {})

    def test_un_hote_invalide_ne_sert_rien_meme_si_la_carte_le_porte(self):
        """La garde de `_hote()` mesurée seule : une carte qui porterait une clé impossible.

        Par HTTP, les deux gardes se couvrent (une entrée invalide n'entre pas dans la carte),
        donc retirer celle-ci ne changeait aucune réponse. Ici, la carte est forcée.
        """
        with patch.object(ctrl, "_carte", lambda: {"pas-un-domaine": "migadu"}), \
                patch.object(ctrl, "request", _Requete("pas-un-domaine", {})):
            self.assertEqual(ctrl._hote(), "")
