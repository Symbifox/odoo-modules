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

from cryptography.fernet import Fernet

from odoo.tests.common import TransactionCase, tagged

from odoo.addons.bf_claude_chat.models.res_config_settings import (
    MASQUE_CLE,
    ResConfigSettings,
)

MASQUE = MASQUE_CLE
PARAM = "bf_claude_chat.api_key_encrypted"
CHAMP = "claude_api_key"


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
        # 🔴 Le masque de ce module dit « une clé UTILISABLE », pas « le
        # paramètre n'est pas vide » : il DÉCHIFFRE. Ces essais exercent donc
        # le vrai Fernet, avec une clé jetable, et ne dépendent d'aucune clé
        # configurée sur l'hôte.
        cls.cle = Fernet.generate_key()
        cls.startClassPatcher(patch.object(
            ResConfigSettings, "_get_encryption_key",
            staticmethod(lambda: cls.cle)))

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

    def _poser_un_secret(self, valeur="sk-ant-de-l-essai"):
        """Pose une clé RÉELLEMENT chiffrée, avec la clé jetable de la classe.

        Pas une chaîne arbitraire : l'écran de ce module déchiffre pour savoir
        si la clé est utilisable, et une valeur illisible doit s'afficher comme
        « aucune clé » — c'est justement ce que `test_une_cle_illisible_ne_se_
        montre_pas_comme_posee` éprouve.
        """
        self.ICP.set_param(PARAM, ResConfigSettings._encrypt_api_key(valeur))
        self.env.flush_all()
        return valeur

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

    def _clair_en_base(self):
        return ResConfigSettings._decrypt_api_key(
            self.env, self.ICP.get_param(PARAM, ""))

    def test_un_enregistrement_neutre_ne_touche_pas_au_secret(self):
        """Le geste qui a vidé la politique de conservation ailleurs :
        enregistrer depuis un autre onglet.

        On renvoie au serveur exactement ce que le formulaire portait.
        """
        self._poser_un_secret("a-garder")
        porte = self._ce_que_le_formulaire_recoit()
        self.Reglages.create({nom: val for nom, val in porte.items()
                             if nom in self.Reglages._fields})
        self.env.flush_all()
        self.assertEqual(self._clair_en_base(), "a-garder")

    def test_un_create_vide_d_un_autre_module_ne_touche_pas_au_secret(self):
        """`_add_missing_default_values` appelle `default_get` pour TOUS les
        champs manquants, calculés compris : depuis la surcharge, ce module
        porte ses champs dans CHAQUE `res.config.settings.create()`, et son
        `inverse` tourne. Il doit être inoffensif."""
        self._poser_un_secret("a-garder")
        self.Reglages.create({})
        self.env.flush_all()
        self.assertEqual(self._clair_en_base(), "a-garder")

    def test_le_masque_renvoye_a_la_main_ne_touche_pas_au_secret(self):
        self._poser_un_secret("a-garder")
        self.Reglages.create({CHAMP: MASQUE})
        self.env.flush_all()
        self.assertEqual(self._clair_en_base(), "a-garder")

    def test_le_vide_vaut_inchange_et_non_efface(self):
        self._poser_un_secret("a-garder")
        self.Reglages.create({CHAMP: ""})
        self.env.flush_all()
        self.assertEqual(self._clair_en_base(), "a-garder")

    def test_une_valeur_saisie_remplace_le_secret(self):
        """Le vrai Fernet tourne ici : pas de bouchon de chiffrement."""
        self._poser_un_secret("la-vieille")
        self.Reglages.create({CHAMP: "la-neuve"})
        self.env.flush_all()
        self.assertEqual(self._clair_en_base(), "la-neuve")
        self.assertNotIn("la-neuve", self.ICP.get_param(PARAM, ""),
                         "la clé doit être CHIFFRÉE en base, pas en clair")

    def test_une_cle_illisible_ne_se_montre_pas_comme_posee(self):
        """🔴 Le masque dit « une clé utilisable », pas « le paramètre n'est
        pas vide ». Après une rotation de clé Fernet, l'écran affirmait qu'une
        clé était posée pendant que Gen repartait sur le forfait partagé.
        """
        self.ICP.set_param(PARAM, "pas-un-jeton-fernet")
        self.env.flush_all()
        self.assertFalse(self._ce_que_le_formulaire_recoit().get(CHAMP))

    def test_la_cle_en_clair_heritee_se_montre(self):
        """🔴 `bf_claude_chat.api_key` n'est plus balayé à chaque
        enregistrement : l'écran doit donc la MONTRER, sinon le bouton qui la
        retire reste hors de portée de qui pourrait s'en servir."""
        self.ICP.set_param(PARAM, "")
        self.ICP.set_param("bf_claude_chat.api_key", "clair-herite")
        self.env.flush_all()
        self.assertEqual(self._ce_que_le_formulaire_recoit().get(CHAMP), MASQUE)

    def test_un_espace_seul_ne_passe_pas_pour_une_saisie(self):
        self._poser_un_secret("a-garder")
        self.Reglages.create({CHAMP: "   "})
        self.env.flush_all()
        self.assertEqual(self._clair_en_base(), "a-garder")

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


@tagged("post_install", "-at_install")
class TestBoutonRetirerLaCle(TransactionCase):
    """Retirer la clé est désormais un GESTE, parce que le vide ne l'est plus."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ICP = cls.env["ir.config_parameter"].sudo()
        cls.Reglages = cls.env["res.config.settings"]

    def test_le_bouton_retire_les_deux_parametres(self):
        self.ICP.set_param(PARAM, "chiffre")
        self.ICP.set_param("bf_claude_chat.api_key", "ancien-clair")
        reglages = self.Reglages.create({})
        res = reglages.action_claude_remove_api_key()
        self.env.flush_all()
        self.assertFalse(self.ICP.get_param(PARAM))
        self.assertFalse(self.ICP.get_param("bf_claude_chat.api_key"))
        self.assertEqual(res["params"]["type"], "success")

    def test_le_bouton_sans_cle_ne_crie_pas_victoire(self):
        self.ICP.set_param(PARAM, "")
        self.ICP.set_param("bf_claude_chat.api_key", "")
        reglages = self.Reglages.create({})
        res = reglages.action_claude_remove_api_key()
        self.assertEqual(res["params"]["type"], "info")

    @staticmethod
    def _texte(arch):
        """L'arche de la vue, en texte.

        🔴 `get_views(...)["arch"]` arrive parfois en OCTETS (mesuré sur un
        locataire de production) : un motif texte y lève `TypeError`, et
        l'essai rougirait à la pose sans que le correctif y soit pour rien.
        """
        return arch.decode("utf-8") if isinstance(arch, bytes) else arch

    def test_le_bouton_est_dans_la_vue_et_toujours_visible(self):
        """🔴 Il portait `invisible="not claude_api_key"` : il disparaissait AU
        MOMENT où l'administrateur efface les points, donc exactement quand il
        veut retirer la clé (relecture adverse)."""
        arch = self._texte(
            self.Reglages.get_views([(False, "form")])["views"]["form"]["arch"])
        self.assertIn("action_claude_remove_api_key", arch,
                      "le bouton a disparu de la vue")
        self.assertNotIn('invisible="not claude_api_key"', arch,
                         "le bouton se cache quand on efface les points")

    def test_le_bouton_retire_aussi_une_cle_en_clair_restee_seule(self):
        """Le paramètre hérité en clair n'est plus balayé par chaque
        enregistrement : le bouton doit le prendre."""
        self.ICP.set_param(PARAM, "")
        self.ICP.set_param("bf_claude_chat.api_key", "ancien-clair")
        reglages = self.Reglages.create({})
        res = reglages.action_claude_remove_api_key()
        self.env.flush_all()
        self.assertFalse(self.ICP.get_param("bf_claude_chat.api_key"))
        self.assertEqual(res["params"]["type"], "success")


@tagged("post_install", "-at_install")
class TestBasculeDeForfait(TransactionCase):
    """La bascule de forfait vit dans le pont, pas en base : elle n'a PAS le défaut.

    🔴 Elle n'est volontairement pas alimentée par `default_get` — sa lecture est
    un appel réseau, et `default_get` tourne à chaque
    `res.config.settings.create()` de n'importe quel module. Ce qui la tient,
    et que ces essais éprouvent :
      1. `@api.depends("company_id")` est une dépendance RÉELLE : le `compute`
         tourne sur le chemin du formulaire ;
      2. un `create({})` ne porte pas ces champs, donc l'`inverse` ne tourne pas ;
      3. un pont muet ne fait pas retirer la bascule par un enregistrement
         venu d'ailleurs, parce que `_plan_switch_write` relit avant d'écrire.
    Le pont est remplacé par un faux pont : c'est la VRAIE logique de lecture et
    d'écriture qui tourne, pas une imitation.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Reglages = cls.env["res.config.settings"]
        cls.Compte = cls.env["claude.account"]
        cls.compte = cls.Compte.create({
            "name": "Forfait du banc", "config_dir": "/banc/bascule"})
        # ⚠️ PAS `self.env.uid` : uid 1 (`__system__`) est ARCHIVÉ, et la
        # lecture d'un many2many le filtre (`active_test`). L'essai mesurait
        # alors un ensemble vide qui n'en était pas un.
        cls.usager = cls.env["res.users"].search(
            [("share", "=", False), ("active", "=", True), ("id", "!=", 1)],
            limit=1)

    def _faux_pont(self, gestes, muet=False, muet_jusqu_a=0):
        """Remplace le seul appel sortant. `gestes` reçoit chaque demande.

        `muet_jusqu_a=n` : les n PREMIERS appels lèvent, les suivants répondent.
        C'est la fenêtre qui mord — pont muet à l'OUVERTURE des Paramètres,
        vivant à l'ENREGISTREMENT — et un faux pont muet du début à la fin ne
        la mesure jamais.
        """
        etat = {"config_dir": self.compte.config_dir,
                "users": [self.usager.id],
                "targets": [self.compte.config_dir]}

        def appel(modele, charge):
            gestes.append(charge)
            if muet or len(gestes) <= muet_jusqu_a:
                raise ValueError("Bridge HTTP 500: banc")
            if charge.get("action") == "get":
                return dict(etat)
            etat["config_dir"] = charge.get("config_dir") or ""
            etat["users"] = charge.get("users") or []
            return {"ok": True}

        self.etat_du_pont = etat
        return patch.object(type(self.Compte), "_plan_switch_call", appel)

    @staticmethod
    def _id_de(valeur):
        """L'id d'un many2one rendu par `onchange`.

        Sans sous-spec (`{"fields": {...}}`), la couche web rend l'id nu ;
        avec, elle rend `{"id": ..., "display_name": ...}`. L'essai accepte
        les deux formes pour ne pas tomber sur la forme plutôt que sur le fond.
        """
        if isinstance(valeur, dict):
            return valeur.get("id")
        return valeur or False

    def _spec_bascule(self):
        return {"claude_plan_account_id": {},
                "claude_plan_user_ids": {"fields": {}},
                "claude_plan_switch_snapshot": {},
                "claude_plan_switch_available": {},
                "company_id": {}}

    # ------------------------------------------------------------------
    def test_le_formulaire_voit_la_bascule(self):
        """1. Le `compute` tourne bien sur le chemin du formulaire.

        Si la dépendance à `company_id` cessait d'être réelle, l'instantané
        serait semé à `False` et cet essai tomberait.
        """
        gestes = []
        with self._faux_pont(gestes):
            recu = self.Reglages.onchange({}, [], self._spec_bascule()).get("value", {})
        self.assertTrue(recu.get("claude_plan_switch_snapshot"),
                        "instantané vide : le calcul n'a pas tourné sur le "
                        "chemin du formulaire")
        self.assertEqual(self._id_de(recu.get("claude_plan_account_id")),
                         self.compte.id)
        self.assertNotIn("set", [g.get("action") for g in gestes])

    def test_un_create_vide_ne_touche_pas_a_la_bascule(self):
        """2. `default_get` ne porte pas ces champs : l'`inverse` ne tourne pas."""
        gestes = []
        with self._faux_pont(gestes):
            self.Reglages.create({})
            self.env.flush_all()
        self.assertNotIn("set", [g.get("action") for g in gestes])

    def test_un_enregistrement_neutre_ne_retire_pas_la_bascule(self):
        """Le geste du défaut voisin, appliqué à la bascule : renvoyer ce que le
        formulaire portait ne doit rien changer au pont."""
        gestes = []
        with self._faux_pont(gestes):
            recu = self.Reglages.onchange({}, [], self._spec_bascule()).get("value", {})
            self.Reglages.create({
                "claude_plan_account_id": self._id_de(recu.get("claude_plan_account_id")),
                "claude_plan_user_ids": [(6, 0, [self.usager.id])],
                "claude_plan_switch_snapshot": recu.get("claude_plan_switch_snapshot"),
            })
            self.env.flush_all()
        self.assertNotIn("set", [g.get("action") for g in gestes])

    def test_un_pont_muet_ne_retire_pas_la_bascule(self):
        """3. Pont injoignable de bout en bout : rien ne s'écrit."""
        gestes = []
        with self._faux_pont(gestes, muet=True):
            recu = self.Reglages.onchange({}, [], self._spec_bascule()).get("value", {})
            self.assertFalse(recu.get("claude_plan_switch_snapshot"))
            self.Reglages.create({
                "claude_plan_account_id": False,
                "claude_plan_user_ids": [(6, 0, [])],
                "claude_plan_switch_snapshot": recu.get("claude_plan_switch_snapshot") or "",
            })
            self.env.flush_all()
        self.assertNotIn("set", [g.get("action") for g in gestes])

    def test_un_pont_muet_a_l_ouverture_puis_vivant_ne_retire_pas_la_bascule(self):
        """🔴 La fenêtre qui mord, trouvée par la relecture adverse.

        Le pont ne répond pas quand les Paramètres s'ouvrent : le calcul rend
        son erreur, donc un instantané VIDE et un compte vide. L'admin
        enregistre un réglage d'un autre onglet. Le pont, lui, répond de
        nouveau. Sans la garde de l'instantané, `_plan_switch_write` relit une
        bascule en place, voit qu'on lui demande le vide, et la RETIRE — un
        formulaire qui ne l'a jamais vue l'efface.
        """
        gestes = []
        with self._faux_pont(gestes, muet_jusqu_a=1):
            recu = self.Reglages.onchange({}, [], self._spec_bascule()).get("value", {})
            self.assertFalse(recu.get("claude_plan_switch_snapshot"),
                             "l'écran devrait rendre un instantané vide")
            self.Reglages.create({
                "claude_plan_account_id": False,
                "claude_plan_user_ids": [(6, 0, [])],
                "claude_plan_switch_snapshot": recu.get("claude_plan_switch_snapshot") or "",
            })
            self.env.flush_all()
        self.assertNotIn("set", [g.get("action") for g in gestes],
                         "la bascule a été retirée par un formulaire qui ne "
                         "l'avait jamais lue")
        self.assertEqual(self.etat_du_pont["config_dir"], self.compte.config_dir)

    def test_un_create_qui_ne_porte_que_l_instantane_ne_retire_rien(self):
        """Variante du même trou : l'instantané partage désormais l'`inverse`
        des deux autres champs, donc un `create` qui ne porte QUE lui atteint
        `_inverse_plan_switch`, avec un compte et des usagers non calculés."""
        gestes = []
        with self._faux_pont(gestes):
            self.Reglages.create({"claude_plan_switch_snapshot": "PERIME"})
            self.env.flush_all()
        self.assertNotIn("set", [g.get("action") for g in gestes])

    def test_l_instantane_atteint_bien_l_inverse(self):
        """🔴 Le garde-fou ne vaut que s'il arrive jusqu'à l'`inverse`.

        Odoo groupe les champs par méthode d'`inverse` et n'alimente le cache
        que du groupe courant : avec un `inverse` à lui, l'instantané valait
        `False` dans `_inverse_plan_switch`, sur le chemin `create` qui est le
        seul que la page Paramètres emprunte.
        """
        vu = []
        Reglages = type(self.Reglages)
        vrai = Reglages._inverse_plan_switch

        def espion(reglages):
            for rec in reglages:
                vu.append(rec.claude_plan_switch_snapshot)
            return vrai(reglages)

        gestes = []
        with self._faux_pont(gestes), \
                patch.object(Reglages, "_inverse_plan_switch", espion):
            self.Reglages.create({
                "claude_plan_account_id": self.compte.id,
                "claude_plan_user_ids": [(6, 0, [self.usager.id])],
                "claude_plan_switch_snapshot": "INSTANTANE-DU-FORMULAIRE",
            })
            self.env.flush_all()
        self.assertEqual(vu, ["INSTANTANE-DU-FORMULAIRE"])

    def test_un_formulaire_perime_ne_revient_pas_sur_une_bascule_faite_ailleurs(self):
        """Ce que le garde-fou protège vraiment.

        Le formulaire a lu l'état A. Pendant qu'il était ouvert, le skill a
        basculé vers « forfait de l'instance ». En enregistrant un réglage sans
        rapport, le formulaire renvoie A : il ne doit PAS écrire.
        """
        gestes = []
        with self._faux_pont(gestes):
            lu = self.Compte._plan_switch_snapshot()        # l'état A
            # la bascule change ailleurs : plus de forfait, plus d'usagers
            self.Compte._plan_switch_call({"action": "set", "config_dir": "",
                                           "users": []})
            gestes.clear()
            # le formulaire périmé renvoie A, sans que personne y ait touché
            self.Reglages.create({
                "claude_plan_account_id": self.compte.id,
                "claude_plan_user_ids": [(6, 0, [self.usager.id])],
                "claude_plan_switch_snapshot": lu,
            })
            self.env.flush_all()
        self.assertNotIn("set", [g.get("action") for g in gestes],
                         "un formulaire périmé a réécrit la bascule")

    def test_une_bascule_demandee_passe(self):
        """Un changement réel, lui, atteint le pont."""
        gestes = []
        with self._faux_pont(gestes):
            instantane = self.Compte._plan_switch_snapshot()
            self.Reglages.create({
                "claude_plan_account_id": False,
                "claude_plan_user_ids": [(6, 0, [])],
                "claude_plan_switch_snapshot": instantane,
            })
            self.env.flush_all()
        poses = [g for g in gestes if g.get("action") == "set"]
        self.assertEqual(len(poses), 1)
        self.assertEqual(poses[0]["config_dir"], "")
