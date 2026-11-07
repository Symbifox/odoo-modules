"""Les portes du pont : qui a le droit d'écrire au registre de consommation.

Quatre méthodes de ce module sont PUBLIQUES, donc appelables par `call_kw`
par n'importe quelle session connectée, portail compris, sans qu'aucune ACL ne
s'applique à l'appel d'une méthode. Trois d'entre elles touchent la base en
`sudo()`, et la première est la pire :

* `claude.chat.message.journaliser_passe` crée une conversation, au besoin un
  compte, et une ligne de registre, en **attribuant l'usage au `user_id`
  reçu en argument**. Une fausse consommation s'inscrivait donc au nom de
  n'importe qui ;
* `claude.account.enregistrer_releve` **remplace** les fenêtres de quota d'un
  compte, donc en détruit ;
* `claude.account.seuils_du_compte` rend les seuils d'un compte, que l'ACL
  réserve à l'administration ;
* `claude.account.compte_par_repertoire` ouvre un compte.

Elles doivent rester publiques : le pont les appelle par XML-RPC. Ce qui
manquait n'est donc pas une privatisation, c'est le test du QUI.
`garde_pont.exiger_le_pont` ouvre à trois comptes seulement : le superutilisateur, un
administrateur, ou un membre du groupe « Pont GenFox (RPC) », que porte le
compte technique sous lequel le pont s'authentifie.

Ces essais tiennent les deux bouts : le refus pour tous les autres, et le
passage pour le pont. Une garde qui fermerait aussi la porte au pont
couperait le registre sans que rien ne le dise, puisque le pont et la sonde
avalent leurs propres échecs.

⚠️ Ce qui ne se prouve PAS avec `assertRaises` : « aucune ligne n'a été
écrite ». Le bloc entre un point de sauvegarde et le défait quand l'exception
attendue tombe, donc un compteur relu après lui rend sa valeur d'avant, que la
méthode ait écrit ou non. L'essai qui mesure vraiment l'absence d'écriture
attrape l'exception à la main : `test_rien_ne_s_inscrit_au_nom_d_un_autre`.
"""

import json

from odoo.exceptions import AccessError
from odoo.service.model import get_public_method
from odoo.tests.common import HttpCase, TransactionCase, new_test_user, tagged
from odoo.tools import mute_logger

PORTES = (
    ("claude.chat.message", "journaliser_passe"),
    ("claude.account", "enregistrer_releve"),
    ("claude.account", "seuils_du_compte"),
    ("claude.account", "compte_par_repertoire"),
)

REPERTOIRE = "/essai/pont/.claude"


def _charge(utilisation=22.0):
    """Une charge utile de /api/oauth/usage, réduite à ce qui suffit."""
    return {"subscription_type": "max",
            "five_hour": {"utilization": utilisation,
                          "resets_at": "2026-12-01T13:00:00Z"}}


@tagged("post_install", "-at_install")
class TestPortesDuPont(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Message = cls.env["claude.chat.message"]
        cls.Compte = cls.env["claude.account"]
        cls.portail = new_test_user(cls.env, login="banc_pont_portail",
                                    groups="base.group_portal")
        cls.interne = new_test_user(cls.env, login="banc_pont_interne",
                                    groups="base.group_user")
        cls.pont = new_test_user(cls.env, login="banc_pont_robot",
                                 groups="base.group_user,bf_claude_chat.group_pont")
        cls.admin = new_test_user(cls.env, login="banc_pont_admin",
                                  groups="base.group_user,base.group_system")

    # ── Le modèle de menace ───────────────────────────────────────────
    def test_les_quatre_portes_restent_des_portes_rpc(self):
        """Le correctif est un test du QUI, pas une privatisation : si l'une
        de ces méthodes devenait privée, le pont perdrait son entrée et le
        registre se tairait sans erreur. Cet essai dit qu'elles sont bien
        appelables à distance, donc que la garde est la SEULE chose qui les
        ferme."""
        for modele, nom in PORTES:
            with self.subTest(porte=f"{modele}.{nom}"):
                self.assertTrue(get_public_method(
                    self.env[modele].with_user(self.interne), nom))

    # ── journaliser_passe ─────────────────────────────────────────────
    def test_un_portail_ne_journalise_pas_une_passe(self):
        with self.assertRaises(AccessError):
            self.Message.with_user(self.portail).journaliser_passe(
                "refine_meeting", {"input_tokens": 1000})

    def test_un_interne_ordinaire_ne_journalise_pas_une_passe(self):
        """Le portail n'est pas le seul danger : un compte interne ordinaire
        (un employé, un stagiaire) n'a rien à inscrire au registre non plus."""
        with self.assertRaises(AccessError):
            self.Message.with_user(self.interne).journaliser_passe(
                "refine_meeting", {"input_tokens": 1000})

    def test_le_pont_journalise_une_passe(self):
        ligne = self.Message.with_user(self.pont).journaliser_passe(
            "refine_meeting", {"input_tokens": 1200, "output_tokens": 300,
                               "cost_usd": 0.42},
            "Raffinage d'un compte rendu", "meeting.record", 341,
            self.interne.id)
        self.assertTrue(ligne, "le pont doit passer : sinon le registre se tait")
        message = self.Message.browse(ligne)
        self.assertEqual(message.input_tokens, 1200)
        self.assertEqual(message.session_id.origin, "refine_meeting")
        self.assertEqual(message.session_id.res_id, 341)
        self.assertEqual(message.user_id, self.interne,
                         "l'usage reste attribué à la personne que le pont nomme")
        self.assertTrue(message.internal, "une passe n'est pas une prise de parole")

    def test_rien_ne_s_inscrit_au_nom_d_un_autre(self):
        """La mesure de l'absence d'écriture, sans `assertRaises`.

        ⚠️ Un `assertRaises` défait ici tout ce que le bloc aurait écrit : le
        compteur relu après lui serait le même que la garde ait tenu ou non,
        et l'essai passerait au vert sur une porte grande ouverte. On attrape
        donc l'exception à la main : la garde lève avant toute écriture, la
        transaction reste utilisable, et le compteur qui suit a un sens.
        """
        avant = self.Message.search_count([("user_id", "=", self.interne.id)])
        leve = False
        try:
            self.Message.with_user(self.portail).journaliser_passe(
                "web", {"input_tokens": 999_999}, "fausse consommation",
                False, False, self.interne.id)
        except AccessError:
            leve = True
        self.assertTrue(leve, "la porte doit refuser")
        self.assertEqual(
            self.Message.search_count([("user_id", "=", self.interne.id)]), avant,
            "aucune ligne de registre au nom de quelqu'un d'autre")

    # ── enregistrer_releve ────────────────────────────────────────────
    def test_un_portail_ne_remplace_pas_les_fenetres_de_quota(self):
        """Celle-ci DÉTRUIT : le relevé remplace les fenêtres du compte."""
        with self.assertRaises(AccessError):
            self.Compte.with_user(self.portail).enregistrer_releve(
                REPERTOIRE, charge=_charge())

    def test_le_pont_enregistre_un_releve(self):
        cid = self.Compte.with_user(self.pont).enregistrer_releve(
            REPERTOIRE, nom="Compte d'essai", charge=_charge(31.5))
        self.assertTrue(cid, "le pont doit passer : sinon la sonde ne verse plus")
        compte = self.Compte.browse(cid)
        self.assertEqual(compte.config_dir, REPERTOIRE)
        self.assertEqual(compte.window_ids.filtered(
            lambda f: f.fenetre == "five_hour").utilization, 31.5)

    # ── seuils_du_compte ──────────────────────────────────────────────
    def test_un_portail_ne_lit_pas_les_seuils(self):
        """⚠️ La garde est AVANT le `try` de la méthode. À l'intérieur, son
        `except Exception` l'avalerait et la porte rendrait `{}` : un refus
        qui ressemble à « compte inconnu », donc une faille qui se lit comme
        un correctif."""
        self.Compte.with_user(self.pont).enregistrer_releve(
            REPERTOIRE, charge=_charge())
        with self.assertRaises(AccessError):
            self.Compte.with_user(self.portail).seuils_du_compte(REPERTOIRE)

    def test_le_pont_lit_les_seuils(self):
        self.Compte.with_user(self.pont).enregistrer_releve(
            REPERTOIRE, charge=_charge())
        seuils = self.Compte.with_user(self.pont).seuils_du_compte(REPERTOIRE)
        self.assertTrue(seuils, "la sonde n'est pas administratrice : sans ces "
                                "seuils, la règle vivrait en double")
        self.assertIn("seuil_haut", seuils)

    # ── compte_par_repertoire ─────────────────────────────────────────
    def test_un_portail_n_ouvre_pas_de_compte(self):
        """🔴 L'assertion porte sur la PROVENANCE du refus, pas sur sa classe.

        L'ACL de `claude.account` (administration seulement) refuse déjà un
        portail sur le `search` de cette porte : un simple
        `assertRaises(AccessError)` restait vert la garde retirée, et c'est
        une relecture adverse qui l'a mesuré. Le libellé du refus est la seule
        chose qui distingue les deux portes closes.
        """
        with self.assertRaisesRegex(AccessError, "pont GenFox"):
            self.Compte.with_user(self.portail).compte_par_repertoire(
                "/essai/pont/forge")

    def test_un_interne_qui_a_l_ACL_est_quand_meme_refuse(self):
        """La garde isolée de l'ACL : on donne l'accès au modèle, et on mesure.

        C'est l'essai qui dit que `compte_par_repertoire` est fermée par la
        garde et pas seulement par son ACL. L'octroi vit dans la transaction
        de l'essai, donc il ne survit pas à sa fin.
        """
        self.env["ir.model.access"].create({
            "name": "essai : claude.account aux internes",
            "model_id": self.env["ir.model"]._get("claude.account").id,
            "group_id": self.env.ref("base.group_user").id,
            "perm_read": True, "perm_create": True,
        })
        self.env.registry.clear_cache()
        with self.assertRaisesRegex(AccessError, "pont GenFox"):
            self.Compte.with_user(self.interne).compte_par_repertoire(
                "/essai/pont/acl")

    # ── L'autre moitié de la garde ────────────────────────────────────
    def test_un_administrateur_passe(self):
        """Un pont peut être branché sur un compte administrateur plutôt que
        sur le compte technique : fermer la porte aux administrateurs
        couperait le registre de ces installations."""
        self.assertTrue(self.Message.with_user(self.admin).journaliser_passe(
            "title", {"input_tokens": 10}))


@tagged("post_install", "-at_install")
class TestPortesDuPontParRpc(HttpCase):
    """Le vrai chemin : `call_kw` sur une session de portail connectée.

    L'essai ORM prouve la garde ; celui-ci prouve le parcours exact que
    décrivait le constat : « n'importe quelle session connectée, portail
    compris, peut l'appeler par call_kw ».
    """

    def setUp(self):
        super().setUp()
        self.portail = new_test_user(self.env, login="banc_pont_rpc",
                                     groups="base.group_portal")

    def _call_kw(self, modele, methode, args):
        return self.url_open(
            "/web/dataset/call_kw",
            data=json.dumps({"jsonrpc": "2.0", "method": "call",
                             "params": {"model": modele, "method": methode,
                                        "args": args, "kwargs": {}}}),
            headers={"Content-Type": "application/json"}, timeout=30).json()

    @mute_logger("odoo.http", "odoo.sql_db")
    def test_un_portail_connecte_est_refuse_par_call_kw(self):
        self.authenticate("banc_pont_rpc", "banc_pont_rpc")
        reponse = self._call_kw("claude.chat.message", "journaliser_passe",
                                ["web", {"input_tokens": 999_999}])
        self.assertIn("error", reponse,
                      "call_kw doit rendre une erreur, pas un identifiant")
        self.assertEqual(reponse["error"]["data"]["name"],
                         "odoo.exceptions.AccessError")
        self.assertNotIn("result", reponse)
