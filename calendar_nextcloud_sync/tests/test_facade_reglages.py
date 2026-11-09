"""La façade de l'écran des Paramètres : ce que le FORMULAIRE reçoit.

🔴 Le chemin à éprouver est `onchange`, jamais `create({})`. La page
Paramètres d'Odoo 18 se bâtit par `onchange`, et la couche web
(`addons/web/models/models.py`) sème à `False` tout champ de la vue que
`default_get` ne rend pas. Le `compute` d'un champ de façade ne tourne donc
JAMAIS à l'affichage, tandis que `create({})` déclenche le calcul et ne
montre rien du défaut. C'est ce qui a laissé passer le défaut voisin pendant deux mois.

⚠️ La spec d'`onchange` est bâtie sur la VRAIE vue des Paramètres, mais réduite
aux champs de ce module plus `company_id` : la page entière déclenche le calcul
de tous les onglets de tous les modules installés, dont certains sortent sur le
réseau. Le mécanisme est le même — `company_id` porte un défaut, donc il entre
dans les valeurs « changées » et `record.modified(todo)` invalide ce qui en
dépend ; un champ sans dépendance réelle, lui, reste au `False` semé.
"""
import re

from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged

from odoo.addons.calendar_nextcloud_sync.models.res_config_settings import (
    MASQUE_SECRET,
)

MASQUE = MASQUE_SECRET
PARAM = "calendar_nextcloud_sync.google_oauth_client_secret_enc"
CHAMP = "google_oauth_client_secret"


# post_install : la vue des Paramètres porte les champs de TOUS les modules
# installés, et un registre partiel (at_install) n'en montre qu'une partie.
@tagged("post_install", "-at_install")
class TestFacadeReglages(TransactionCase):
    """Un enregistrement neutre ne doit RIEN effacer, et l'écran ne doit pas mentir."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ICP = cls.env["ir.config_parameter"].sudo()
        cls.Reglages = cls.env["res.config.settings"]
        cls.arch = cls._texte(
            cls.Reglages.get_views([(False, "form")])["views"]["form"]["arch"])

    # ------------------------------------------------------------------
    @staticmethod
    def _texte(arch):
        """L'arche de la vue, en texte.

        🔴 `get_views(...)["arch"]` arrive parfois en OCTETS (mesuré sur un
        locataire de production) : un motif texte y lève `TypeError`, et
        l'essai rougirait à la pose sans que le correctif y soit pour rien.
        """
        return arch.decode("utf-8") if isinstance(arch, bytes) else arch

    def _spec(self):
        """La spec que le navigateur enverrait, réduite à ce module.

        `company_id` y reste : il porte un défaut, donc il entre dans les
        valeurs « changées » du premier appel, et c'est lui qui fait recalculer
        tout champ qui en dépend réellement.
        """
        presents = set(re.findall(r'<field name="([^"]+)"', self.arch))
        self.assertIn(CHAMP, presents,
                      "le champ a disparu de la vue des Paramètres : "
                      "l'essai ne mesure plus rien")
        spec = {}
        for nom in [CHAMP, "company_id"]:
            champ = self.Reglages._fields[nom]
            spec[nom] = {"fields": {}} if champ.type in ("one2many", "many2many") else {}
        return spec

    def _ce_que_le_formulaire_recoit(self):
        return self.Reglages.onchange({}, [], self._spec()).get("value", {})

    def _poser_un_secret(self, valeur="chiffre-du-banc"):
        """Pose un secret en base. Sa forme est sans importance : l'écran ne
        regarde que sa PRÉSENCE, et aucune clé Fernet n'est donc requise."""
        self.ICP.set_param(PARAM, valeur)
        self.env.flush_all()

    # ------------------------------------------------------------------
    def test_le_formulaire_montre_le_masque_quand_un_secret_est_pose(self):
        self._poser_un_secret()
        recu = self._ce_que_le_formulaire_recoit()
        self.assertEqual(recu.get(CHAMP), MASQUE)

    def test_le_formulaire_montre_vide_sans_secret(self):
        self.ICP.set_param(PARAM, "")
        self.env.flush_all()
        recu = self._ce_que_le_formulaire_recoit()
        self.assertFalse(recu.get(CHAMP))

    def test_un_enregistrement_neutre_ne_touche_pas_au_secret(self):
        """Le geste qui a vidé la politique de conservation ailleurs :
        enregistrer depuis un autre onglet.

        On renvoie au serveur exactement ce que le formulaire portait.
        """
        self._poser_un_secret("chiffre-a-garder")
        porte = self._ce_que_le_formulaire_recoit()
        self.Reglages.create({nom: val for nom, val in porte.items()
                             if nom in self.Reglages._fields})
        self.env.flush_all()
        self.assertEqual(self.ICP.get_param(PARAM), "chiffre-a-garder")

    def test_un_create_vide_d_un_autre_module_ne_touche_pas_au_secret(self):
        """`_add_missing_default_values` appelle `default_get` pour TOUS les
        champs manquants, calculés compris : depuis la surcharge, ce module
        porte ses champs dans CHAQUE `res.config.settings.create()`, et son
        `inverse` tourne. Il doit être inoffensif."""
        self._poser_un_secret("chiffre-a-garder")
        self.Reglages.create({})
        self.env.flush_all()
        self.assertEqual(self.ICP.get_param(PARAM), "chiffre-a-garder")

    def test_le_masque_renvoye_a_la_main_ne_touche_pas_au_secret(self):
        self._poser_un_secret("chiffre-a-garder")
        self.Reglages.create({CHAMP: MASQUE})
        self.env.flush_all()
        self.assertEqual(self.ICP.get_param(PARAM), "chiffre-a-garder")

    def test_le_vide_vaut_inchange_et_non_efface(self):
        self._poser_un_secret("chiffre-a-garder")
        self.Reglages.create({CHAMP: ""})
        self.env.flush_all()
        self.assertEqual(self.ICP.get_param(PARAM), "chiffre-a-garder")

    def test_une_valeur_saisie_remplace_le_secret(self):
        self._poser_un_secret("vieux-chiffre")
        with patch.object(
                    type(self.env["nextcloud.calendar.sync.config"]),
                    "_encrypt_value", lambda self, v: "CHIFFRE:" + v):
            self.Reglages.create({CHAMP: "nouveau-clair"})
            self.env.flush_all()
        self.assertEqual(self.ICP.get_param(PARAM), "CHIFFRE:nouveau-clair")

    def test_un_espace_seul_ne_passe_pas_pour_une_saisie(self):
        self._poser_un_secret("chiffre-a-garder")
        self.Reglages.create({CHAMP: "   "})
        self.env.flush_all()
        self.assertEqual(self.ICP.get_param(PARAM), "chiffre-a-garder")

    def test_le_compute_ne_rend_que_le_masque(self):
        """La propriété « le secret ne repart jamais vers le navigateur ».

        🔴 Trouvé par la relecture adverse : aucun autre essai ne lit ce champ
        par l'ORM. Remettre le clair dans le `compute` les laisserait tous au
        vert, parce qu'ils passent par `onchange` et `default_get`, où la
        valeur est DONNÉE et le calcul ne tourne pas. `new({})` ne pose aucun
        défaut : lire le champ y déclenche bien le `compute`.
        """
        self._poser_un_secret()
        self.assertEqual(self.Reglages.new({})[CHAMP], MASQUE)

    def test_default_get_ne_rend_rien_quand_le_champ_n_est_pas_demande(self):
        """Le champ absent de `fields_list` ne doit pas apparaître dans le
        résultat : un défaut rendu hors demande entre dans des `create()` qui
        ne portaient pas ce champ."""
        self._poser_un_secret()
        defauts = self.Reglages.default_get(["company_id"])
        self.assertNotIn(CHAMP, defauts)
