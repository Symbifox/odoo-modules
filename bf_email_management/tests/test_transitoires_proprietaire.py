"""Les quatorze assistants `bf.email.*` et leur propriétaire.

Aucun des quatorze modèles
transitoires du module n'avait de règle d'enregistrement, et leur ACL les ouvre
à tout employé en lecture, écriture, création et suppression. Odoo 18 n'impose
plus de restriction implicite au créateur sur un transitoire : un collègue
lisait donc le formulaire d'un autre — mot de passe IMAP et corps de courriel
compris — par un simple `web_search_read`.

Ce que ces essais prouvent :

* pour CHACUN des quatorze, le formulaire de A est fermé à B — la recherche
  est aveugle ET la lecture directe lève — tandis que A garde le sien ;
* le mot de passe quitte la ligne d'assistant sur TOUS les chemins de sortie,
  y compris le refus « il manque le serveur IMAP » et un échec survenu APRÈS
  une connexion réussie ;
* il en part par le bon curseur : dans la transaction quand le chemin
  CONTINUE, par un curseur séparé quand le chemin va ANNULER. L'inverse ne
  marche pas, et c'était le défaut de la première version ;
* ouvrir un formulaire neuf efface les vieilles lignes de SON AUTEUR, sans
  rien attendre du cron `base.autovacuum_job` — qui peut être désactivé
  sur une instance, où des lignes de plusieurs semaines dorment alors — et sans
  toucher à celles d'un collègue.

⚠️ `TestBrancherSurUneLigneValidee` VALIDE en base, parce que ce défaut ne
se voit pas autrement : dans un `TransactionCase`, le curseur séparé de
`_oublier` ne voit pas une ligne que l'essai n'a pas validée, son `UPDATE`
touche zéro ligne, et aucun conflit ne se joue. Cette classe retire tout ce
qu'elle a écrit et le vérifie.

Ce qu'ils ne prouvent pas : la contre-épreuve (règle retirée, l'essai tombe)
se joue à la main sur une base d'essai. Un essai ne peut pas défaire la règle qu'il mesure
sans recharger le fichier de sécurité, et il tomberait à sa première assertion
sans dire si les treize autres règles tiennent.
"""

from unittest.mock import patch

import psycopg2

from odoo import SUPERUSER_ID, api
from odoo.exceptions import AccessError, UserError
from odoo.tests import new_test_user, tagged
from odoo.tests.common import TransactionCase

from ..models import bf_email_imap


class FausseSession:
    """Une session IMAP qui ne touche à rien : aucun essai n'ouvre le réseau."""

    def logout(self):
        return None

# Les quatorze, et le minimum qu'il faut pour en poser un en base. L'ordre est
# celui du fichier de sécurité, pour qu'un ajout futur se voie des deux côtés.
QUATORZE = (
    "bf.email.account.setup",
    "bf.email.browser",
    "bf.email.browser.line",
    "bf.email.reroute",
    "bf.email.guess.route",
    "bf.email.guess.route.line",
    "bf.email.report.wizard",
    "bf.email.snooze",
    "bf.email.absence.preview",
    "bf.email.rule.quick",
    "bf.email.rule.quick.line",
    "bf.email.cleanup",
    "bf.email.initial.sync",
    "bf.email.imap.backfill",
)


@tagged("post_install", "-at_install", "isolation_menage")
class TestAssistantsProprietaire(TransactionCase):
    """Le formulaire d'un collègue lui est fermé, l'auteur garde le sien."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.a = new_test_user(
            cls.env, login="essai.a@banc.invalid", groups="base.group_user",
            email="essai.a@banc.invalid")
        cls.b = new_test_user(
            cls.env, login="essai.b@banc.invalid", groups="base.group_user",
            email="essai.b@banc.invalid")
        # ⚠️ Un administrateur système, pour montrer que la règle est GLOBALE :
        # elle s'ajoute par ET à tout le reste, donc même lui ne lit pas le
        # formulaire d'autrui. C'est voulu, comme pour le compte IMAP.
        cls.admin = new_test_user(
            cls.env, login="essai.adm@banc.invalid",
            groups="base.group_user,base.group_system,"
                   "bf_email_management.group_email_admin",
            email="essai.adm@banc.invalid")

        cls.compte = cls.env["bf.email.account"].with_user(cls.a).create({
            "name": "Boîte de A", "host": "imap.banc.invalid", "port": 993,
            "login": "essai.a@banc.invalid", "password": "x",
        })
        cls.courriel = cls.env["bf.email"].with_user(cls.a).create({
            "subject": "Sujet de A", "email_from": "dehors@banc.invalid",
            "direction": "in", "status": "new", "source": "imap",
            "user_id": cls.a.id, "date": "2026-11-01 12:00:00",
            "message_id_header": "<essai-a@banc.invalid>",
        })
        cls.absence = cls.env["bf.email.absence"].with_user(cls.a).create({
            "name": "Absence de A", "user_id": cls.a.id,
            "date_from": "2026-12-01 00:00:00",
            "date_to": "2026-12-15 23:59:00",
        })

    # ------------------------------------------------------------------
    def _poser(self, modele):
        """Une ligne d'assistant, posée par A, pour chacun des quatorze."""
        Assistant = self.env[modele].with_user(self.a)
        if modele == "bf.email.account.setup":
            return Assistant.create({
                "email": "nouvelle@banc.invalid", "state": "detecte",
                "imap_host": "imap.banc.invalid",
                "password": "secret-de-A"})
        if modele == "bf.email.browser":
            return Assistant.create({
                "account_id": self.compte.id, "folder": "INBOX",
                "preview_subject": "Sujet prévisualisé par A",
                "preview_body_html": "<p>Le corps du courriel de A.</p>"})
        if modele == "bf.email.browser.line":
            parent = self._poser("bf.email.browser")
            return Assistant.create({
                "browser_id": parent.id, "uid": "4242",
                "email_from": "dehors@banc.invalid",
                "subject": "Sujet vu par A"})
        if modele == "bf.email.reroute":
            return Assistant.create({"bf_email_ids": [(6, 0, self.courriel.ids)]})
        if modele == "bf.email.guess.route":
            return Assistant.create({})
        if modele == "bf.email.guess.route.line":
            parent = self._poser("bf.email.guess.route")
            return Assistant.create({
                "wizard_id": parent.id, "bf_email_id": self.courriel.id})
        if modele == "bf.email.report.wizard":
            return Assistant.create({
                "email_ids": [(6, 0, self.courriel.ids)], "kind": "spam"})
        if modele == "bf.email.snooze":
            return Assistant.create({
                "bf_email_ids": [(6, 0, self.courriel.ids)],
                "preset": "tomorrow"})
        if modele == "bf.email.absence.preview":
            return Assistant.create({
                "absence_id": self.absence.id, "sender": "dehors@banc.invalid"})
        if modele == "bf.email.rule.quick":
            return Assistant.create({"scope": "user"})
        if modele == "bf.email.rule.quick.line":
            parent = self._poser("bf.email.rule.quick")
            return Assistant.create({
                "wizard_id": parent.id, "recipe_key": "essai",
                "name": "Règle d'essai de A"})
        if modele == "bf.email.cleanup":
            return Assistant.create({"days": 30})
        if modele == "bf.email.initial.sync":
            return Assistant.create({
                "date_from": "2026-01-01", "date_to": "2026-02-01"})
        if modele == "bf.email.imap.backfill":
            return Assistant.create({
                "account_id": self.compte.id, "folder": "INBOX"})
        raise AssertionError("modèle non couvert : %s" % modele)

    # ------------------------------------------------------------------
    def test_les_quatorze_sont_couverts_par_une_regle(self):
        """La liste des essais et celle du module ne doivent pas diverger.

        🔴 C'est l'essai qui attrape le quinzième assistant : un transitoire
        ajouté au module sans règle fait tomber celui-ci, sans qu'il faille le remarquer.
        """
        transitoires = {
            m.model for m in self.env["ir.model"].search([
                ("transient", "=", True),
                ("model", "=like", "bf.email.%"),
            ])
        }
        self.assertEqual(
            transitoires, set(QUATORZE),
            "un transitoire bf.email.* n'est pas dans la liste des essais")
        for modele in QUATORZE:
            with self.subTest(modele=modele):
                regles = self.env["ir.rule"].sudo().search([
                    ("model_id.model", "=", modele)])
                self.assertTrue(regles, "aucune règle sur %s" % modele)
                for regle in regles:
                    # ⚠️ Le champ s'appelle « global » tout court : le cœur
                    # le pose par `setattr`, parce que c'est un mot-clé Python,
                    # et `regle.global_` lève un AttributeError.
                    self.assertTrue(
                        regle["global"],
                        "la règle de %s n'est pas globale" % modele)
                    self.assertEqual(
                        regle.domain_force, "[('create_uid', '=', user.id)]",
                        "domaine inattendu sur %s" % modele)
                    self.assertTrue(
                        regle.perm_read and regle.perm_write
                        and regle.perm_create and regle.perm_unlink,
                        "les quatre permissions ne sont pas couvertes sur %s"
                        % modele)

    def test_le_formulaire_d_un_collegue_lui_est_ferme(self):
        for modele in QUATORZE:
            with self.subTest(modele=modele):
                ligne = self._poser(modele)
                self.env.invalidate_all()
                # La recherche est aveugle : le formulaire n'existe pas pour B.
                self.assertFalse(
                    self.env[modele].with_user(self.b).search(
                        [("id", "=", ligne.id)]),
                    "B retrouve le formulaire de A sur %s" % modele)
                self.assertEqual(
                    self.env[modele].with_user(self.b).search_count(
                        [("id", "=", ligne.id)]), 0, modele)
                # Et la lecture directe, celle que fait `web_read`, lève.
                with self.assertRaises(AccessError, msg=modele):
                    self.env[modele].with_user(self.b).browse(ligne.id).read(["id"])

    def test_l_auteur_garde_son_formulaire(self):
        for modele in QUATORZE:
            with self.subTest(modele=modele):
                ligne = self._poser(modele)
                self.env.invalidate_all()
                a_lui = self.env[modele].with_user(self.a).browse(ligne.id)
                self.assertTrue(a_lui.read(["id"]), modele)
                self.assertEqual(
                    self.env[modele].with_user(self.a).search_count(
                        [("id", "=", ligne.id)]), 1, modele)

    def test_meme_l_administrateur_ne_lit_pas_le_formulaire_d_autrui(self):
        """La règle est globale : elle vaut aussi pour la zone d'administration.

        Même raisonnement qu'en 18.0.6.0.0 pour `bf.email.account` : ces
        formulaires portent le mot de passe d'un collègue et le corps de son
        courrier, et l'administration du module est en lecture seule ailleurs
        pour exactement cette raison.
        """
        for modele in ("bf.email.account.setup", "bf.email.browser"):
            with self.subTest(modele=modele):
                ligne = self._poser(modele)
                self.env.invalidate_all()
                self.assertFalse(
                    self.env[modele].with_user(self.admin).search(
                        [("id", "=", ligne.id)]), modele)

    def test_le_mot_de_passe_d_un_collegue_ne_sort_pas_par_la_recherche(self):
        """Le chemin exact du défaut : `web_search_read` sur le modèle.

        🔴 Sans règle, cet appel rendait la ligne de A, champ `password`
        compris. C'est ce qu'un collègue pouvait jouer depuis la console du
        navigateur, sans aucun droit particulier.
        """
        ligne = self._poser("bf.email.account.setup")
        self.env.invalidate_all()
        lu = self.env["bf.email.account.setup"].with_user(self.b).web_search_read(
            domain=[], specification={"email": {}, "password": {}})
        self.assertEqual(lu["length"], 0, "B lit encore des formulaires")
        self.assertNotIn(
            "secret-de-A",
            str(lu["records"]), "le mot de passe de A traverse la recherche")
        # Contre-épreuve de la mesure elle-même : A, lui, le retrouve.
        a_lui = self.env["bf.email.account.setup"].with_user(
            self.a).web_search_read(
                domain=[], specification={"email": {}, "password": {}})
        self.assertEqual(a_lui["length"], 1)
        self.assertEqual(a_lui["records"][0]["password"], "secret-de-A")
        self.assertEqual(a_lui["records"][0]["id"], ligne.id)


@tagged("post_install", "-at_install")
class TestMotDePasseNeDortPas(TransactionCase):
    """Le mot de passe quitte la ligne sur TOUS les chemins."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.employe = new_test_user(
            cls.env, login="essai.mdp@banc.invalid", groups="base.group_user",
            email="essai.mdp@banc.invalid")

    def _espion(self):
        """Un curseur séparé qui note le SQL qu'on lui donne.

        Même gabarit que `test_le_mot_de_passe_ne_dort_pas_dans_lassistant` :
        l'effacement ne peut pas se vérifier dans la transaction du
        test, puisque `assertRaises` l'annule — comme la requête HTTP annule
        tout quand une `UserError` remonte. On mesure donc que l'effacement
        passe par un curseur SÉPARÉ, seul moyen qu'il survive à l'annulation.
        """
        joue = []
        registre = self.env["bf.email.account.setup"].pool
        vrai = registre.cursor

        class CurseurEspion:
            def __enter__(self_):
                self_.cr = vrai()
                self_.cr.__enter__()
                execute = self_.cr.execute

                def espion(requete, params=None):
                    joue.append(str(requete))
                    return execute(requete, params)

                self_.cr.execute = espion
                return self_.cr

            def __exit__(self_, *a):
                return self_.cr.__exit__(*a)

        return joue, patch.object(type(registre), "cursor",
                                  lambda *a, **kw: CurseurEspion())

    def _assistant(self, **extra):
        valeurs = {"email": "moi@banc.invalid", "state": "detecte",
                   "imap_host": "imap.banc.invalid",
                   "password": "mot-de-passe-d-application"}
        valeurs.update(extra)
        return self.env["bf.email.account.setup"].with_user(
            self.employe).create(valeurs)

    # ------------------------------------------------------------------
    def test_le_serveur_imap_manquant_efface_quand_meme(self):
        """🔴 Ce refus-là ne passait PAS par `_oublier`.

        Le `web_save` du client a déjà validé le mot de passe dans sa propre
        transaction : l'annulation de celle du bouton ne le défait pas. La
        ligne dormait donc avec son secret jusqu'au ménage — c'est-à-dire pour
        toujours là où le cron est désactivé.
        """
        assistant = self._assistant(imap_host=False)
        joue, espion = self._espion()
        with espion:
            with self.assertRaises(UserError):
                assistant.action_brancher()
        self.assertIn("UPDATE bf_email_account_setup SET password = NULL",
                      " ".join(joue),
                      "le mot de passe reste en base quand le serveur manque")

    def test_un_echec_apres_la_connexion_efface_quand_meme(self):
        """🔴 Tout ce qui suit la connexion peut lever : création du compte,
        identité d'expédition, serveur sortant. Le secret a servi, et ce
        chemin-ci va ANNULER la transaction — ce que le `web_save` du client a
        validé ne se défait pas tout seul. L'effacement passe donc par le
        curseur séparé, seul à survivre à l'annulation."""
        assistant = self._assistant()

        def terminer_qui_casse(self_, compte):
            raise UserError("le serveur sortant refuse")

        joue, espion = self._espion()
        with patch.object(bf_email_imap, "open_connection",
                          lambda *a, **kw: FausseSession()), \
             patch("odoo.addons.bf_email_management.wizard."
                   "bf_email_account_setup.BfEmailAccountSetup._terminer",
                   terminer_qui_casse), \
             patch("odoo.addons.bf_email_management.models.bf_email_account."
                   "BfEmailAccount._semer_le_filigrane",
                   lambda self_, jours=7: True), \
             espion:
            with self.assertRaises(UserError):
                assistant.action_brancher()
        self.assertIn("UPDATE bf_email_account_setup SET password = NULL",
                      " ".join(joue),
                      "le mot de passe survit à un échec d'après-connexion")

    def test_un_echec_rejoue_garde_le_secret_pour_le_rejeu(self):
        """🔴 La branche d'erreur ne doit PAS effacer ce qu'Odoo va rejouer.

        `retrying` rejoue la requête sur une `SerializationFailure`, une
        `DeadlockDetected` ou un `LockNotAvailable` — et le rejeu repart de la
        ligne VALIDÉE. Effacer le mot de passe avant lui le ferait mourir sur
        « Il manque le mot de passe. », c'est-à-dire reproduire C1 sur un
        chemin plus rare. Les autres échecs, eux, effacent (voir les deux
        essais précédents).
        """
        assistant = self._assistant()

        def creer_qui_entre_en_conflit(self_, oauth=False):
            raise psycopg2.errors.SerializationFailure(
                "could not serialize access due to concurrent update")

        joue, espion = self._espion()
        with patch.object(bf_email_imap, "open_connection",
                          lambda *a, **kw: FausseSession()), \
             patch("odoo.addons.bf_email_management.wizard."
                   "bf_email_account_setup.BfEmailAccountSetup."
                   "_creer_le_compte", creer_qui_entre_en_conflit), \
             espion:
            with self.assertRaises(psycopg2.errors.SerializationFailure):
                assistant.action_brancher()
        self.assertNotIn(
            "UPDATE bf_email_account_setup SET password = NULL", " ".join(joue),
            "le secret a été effacé avant un rejeu : le branchement mourra "
            "sur « Il manque le mot de passe. »")
        self.assertEqual(assistant.password, "mot-de-passe-d-application")

    def test_le_chemin_heureux_n_ouvre_aucun_curseur_separe(self):
        """🔴 Sur un chemin qui CONTINUE, l'effacement se fait
        dans la transaction, par l'écriture de la ligne — jamais par un
        curseur séparé.

        Un curseur séparé qui valide au milieu d'une transaction en
        REPEATABLE READ fait rendre « could not serialize access due to
        concurrent update » à l'écriture suivante de la même ligne ; Odoo
        rejoue alors la requête (`retrying`), le mot de passe est déjà parti,
        et la garde du mot de passe lève : zéro compte créé.

        ⚠️ Ce qu'un `TransactionCase` peut mesurer d'ici, c'est le MÉCANISME :
        aucun curseur séparé n'est ouvert sur ce chemin. La CONSÉQUENCE se
        mesure dans `TestBrancherSurUneLigneValidee`, qui valide la ligne.
        L'assertion `assertFalse(assistant.password)` ci-dessous, elle, ne
        prouve rien à elle seule : `_terminer` écrivait déjà `password: False`
        avant le lot. C'est pour ça qu'elle n'est pas seule.
        """
        assistant = self._assistant()
        joue, espion = self._espion()
        with patch.object(bf_email_imap, "open_connection",
                          lambda *a, **kw: FausseSession()), \
             patch("odoo.addons.bf_email_management.models.bf_email_account."
                   "BfEmailAccount._semer_le_filigrane",
                   lambda self_, jours=7: True), \
             espion:
            assistant.action_brancher()
        self.assertNotIn(
            "UPDATE bf_email_account_setup SET password = NULL", " ".join(joue),
            "le chemin heureux efface par un curseur SÉPARÉ : il validerait "
            "au milieu de la transaction, et l'écriture de `_terminer` "
            "rendrait SerializationFailure")
        compte = assistant.account_id
        self.assertTrue(compte, "le compte n'a pas été créé")
        self.assertEqual(compte.sudo().password, "mot-de-passe-d-application")
        self.assertFalse(assistant.password,
                         "la ligne d'assistant garde le mot de passe")
        # Mesuré dans la TABLE, et non dans le cache de l'ORM : c'est bien
        # l'écriture de la ligne qui a emporté le secret.
        self.env.flush_all()
        self.env.cr.execute(
            "SELECT password, state FROM bf_email_account_setup WHERE id = %s",
            (assistant.id,))
        mot, etat = self.env.cr.fetchone()
        self.assertIsNone(mot, "le mot de passe est encore dans la table")
        self.assertEqual(etat, "fini")

    def test_l_oauth_efface_dans_la_transaction_et_sans_conflit(self):
        """🔴 `action_lier_oauth` ÉCRIT la ligne, donc elle ne
        peut pas effacer par un curseur séparé avant — même conflit qu'au branchement.

        ⚠️ Le besoin, lui, est réel : `action_brancher` route vers OAuth AVANT
        la garde du mot de passe, donc une personne qui a tapé un mot de passe
        sur une boîte Microsoft arrive ici avec `password` non nul.
        """
        assistant = self._assistant(auth_mode="oauth", oauth_provider="microsoft")
        joue, espion = self._espion()
        with patch("odoo.addons.bf_email_management.models.bf_email_oauth."
                   "BfEmailOauth.est_configure", lambda self_, f: True), \
             patch("odoo.addons.bf_email_management.models.bf_email_oauth."
                   "BfEmailOauth.url_de_consentement",
                   lambda self_, compte: "https://exemple.invalid/consentir"), \
             espion:
            action = assistant.action_lier_oauth()
        self.assertEqual(action["type"], "ir.actions.act_url")
        self.assertNotIn(
            "UPDATE bf_email_account_setup SET password = NULL", " ".join(joue),
            "l'effacement passe par un curseur séparé alors que cette "
            "méthode écrit la ligne : SerializationFailure")
        self.env.flush_all()
        self.env.cr.execute(
            "SELECT password, account_id FROM bf_email_account_setup "
            "WHERE id = %s", (assistant.id,))
        mot, compte_id = self.env.cr.fetchone()
        self.assertIsNone(mot, "le mot de passe reste sur une ligne OAuth")
        self.assertTrue(compte_id, "le compte en attente n'est pas rattaché")

    def test_l_oauth_refuse_efface_par_le_curseur_separe(self):
        """Les deux refus d'OAuth ANNULENT : eux passent par `_oublier`."""
        assistant = self._assistant(auth_mode="oauth", oauth_provider=False)
        joue, espion = self._espion()
        with espion:
            with self.assertRaises(UserError):
                assistant.action_lier_oauth()
        self.assertIn("UPDATE bf_email_account_setup SET password = NULL",
                      " ".join(joue),
                      "le mot de passe reste quand OAuth refuse")

    def test_ouvrir_un_formulaire_neuf_efface_mes_vieilles_lignes(self):
        """🔴 Le ménage d'Odoo 18 n'a plus qu'un appelant : le cron
        `base.autovacuum_job`, qui peut être désactivé (des lignes de
        plusieurs semaines y dorment alors). Ouvrir l'assistant
        fait donc le ménage lui-même, sinon `_transient_max_hours` ne vaut
        rien."""
        vieille = self._assistant()
        self.env.flush_all()
        # Vieillie à la main, au-delà du quart d'heure de
        # `_transient_max_hours` : c'est le seuil que le module applique.
        self.env.cr.execute(
            "UPDATE bf_email_account_setup "
            "SET create_date = now() - interval '3 hours', "
            "    write_date = now() - interval '3 hours' WHERE id = %s",
            (vieille.id,))
        self.assertTrue(vieille.exists())

        # Et une ligne de dix minutes, elle, reste : le seuil est le quart
        # d'heure de `_transient_max_hours`, lu sur `write_date`.
        recente = self._assistant(email="ouverte-depuis-dix-minutes@banc.invalid")
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE bf_email_account_setup "
            "SET create_date = now() - interval '10 minutes', "
            "    write_date = now() - interval '10 minutes' WHERE id = %s",
            (recente.id,))

        self._assistant(email="autre@banc.invalid")
        self.assertFalse(
            vieille.exists(),
            "la vieille ligne a survécu à l'ouverture d'un formulaire neuf")
        self.assertTrue(
            recente.exists(),
            "un formulaire ouvert depuis dix minutes a été effacé")

    def test_le_menage_epargne_la_vieille_ligne_d_un_autre(self):
        """🔴 Choix assumé : le ménage est borné aux
        lignes de son auteur.

        Le ménage d'Odoo, lui, emporte celles de TOUT LE MONDE au-delà de
        quinze minutes — `_transient_clean_rows_older_than` reçoit
        `_transient_max_hours * 3600` et son plancher de cinq minutes n'est
        qu'un plancher (`max(secondes, 300)`). Or cet assistant envoie
        justement la personne créer un mot de passe d'application chez
        Google, Yahoo ou Apple, ce qui dépasse couramment quinze minutes :
        l'ouverture du formulaire par un collègue détruisait le sien.
        """
        autre = new_test_user(
            self.env, login="essai.mdp2@banc.invalid", groups="base.group_user",
            email="essai.mdp2@banc.invalid")
        vieille = self.env["bf.email.account.setup"].with_user(autre).create({
            "email": "lui@banc.invalid", "state": "detecte",
            "imap_host": "imap.banc.invalid", "password": "secret-de-lui"})
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE bf_email_account_setup "
            "SET create_date = now() - interval '3 hours', "
            "    write_date = now() - interval '3 hours' WHERE id = %s",
            (vieille.id,))
        # L'employé ouvre SON formulaire : la ligne de l'autre reste.
        self._assistant()
        self.assertTrue(
            vieille.sudo().exists(),
            "le ménage a emporté le formulaire d'un collègue")
        # Et la mesure elle-même n'est pas aveugle : la MIENNE, aussi vieille,
        # s'en va bien.
        mienne = self._assistant(email="vieille-a-moi@banc.invalid")
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE bf_email_account_setup "
            "SET create_date = now() - interval '3 hours', "
            "    write_date = now() - interval '3 hours' WHERE id = %s",
            (mienne.id,))
        self._assistant(email="encore-une@banc.invalid")
        self.assertFalse(mienne.exists(), "ma vieille ligne a survécu")

    def test_l_effacement_est_borne_dans_le_temps(self):
        """🔴 Garde-fou : un curseur séparé attendrait SANS FIN le verrou que
        tiendrait CETTE transaction si elle avait déjà écrit la ligne. Un
        travailleur pendu est pire qu'un mot de passe oublié, donc le curseur
        se donne trois secondes et renonce au journal.

        ⚠️ Le conflit lui-même ne se joue pas dans un essai : le curseur
        séparé ne voit pas une ligne que cette transaction n'a pas validée,
        donc il n'y a rien à verrouiller. On mesure que le garde-fou est ARMÉ
        — la seule chose qu'un essai puisse voir d'ici."""
        assistant = self._assistant()
        joue, espion = self._espion()
        with espion:
            assistant._oublier()
        sql = " ".join(joue)
        self.assertIn("SET LOCAL lock_timeout", sql,
                      "l'effacement peut pendre sur un verrou")
        self.assertIn("UPDATE bf_email_account_setup SET password = NULL", sql)


@tagged("post_install", "-at_install")
class TestBrancherSurUneLigneValidee(TransactionCase):
    """🔴 Le seul essai qui pouvait voir le défaut de la première version.

    Pourquoi aucun `TransactionCase` ordinaire ne le voyait :
    `Registry.cursor()` ne rend un curseur PARTAGÉ qu'en mode essai, et
    `enter_test_mode` n'est appelé que par `HttpCase`. Dans un
    `TransactionCase`, le curseur de `_oublier` est une VRAIE connexion
    séparée — qui ne voit pas une ligne que l'essai n'a jamais validée : son
    `UPDATE` touche zéro ligne, ne verrouille rien, et aucune écriture
    suivante n'entre en conflit. Les essais prouvaient que la requête était
    JOUÉE, jamais qu'elle efface.

    Ce que celui-ci fait de plus : il VALIDE la ligne d'assistant dans sa
    propre transaction — c'est le `web_save` du client — puis appuie sur le
    bouton depuis une transaction neuve dont l'instantané est pris AVANT
    l'effacement, comme une requête HTTP qui a déjà lu la session et l'usager.
    Avec le code de la première version, `action_brancher` rend alors
    `SerializationFailure` et crée zéro compte.

    ⚠️ Il écrit donc pour de vrai : un usager, une ligne d'assistant, un
    compte et les règles de boîte semées avec lui. Tout porte la marque
    `essai-valide`, et `_menage` le retire PUIS vérifie qu'il ne reste rien —
    la base d'essai peut être une copie d'une base réelle.
    """

    MARQUE = "essai-valide@banc.invalid"
    SECRET = "mot-de-passe-valide-en-base"

    def setUp(self):
        super().setUp()
        # Idempotent : une passe arrêtée en plein vol ne bloque pas la suivante
        # sur l'unicité du login.
        self._menage(verifier=False)
        self.addCleanup(self._menage)
        with self.registry.cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            # ⚠️ `no_reset_password` : sans lui, `auth_signup` met un courriel
            # d'invitation dans la file de la base — et celle-ci est une copie
            # d'une base réelle, où rien de cet essai n'a à sortir.
            usager = new_test_user(
                env, login=self.MARQUE, groups="base.group_user",
                email=self.MARQUE, context={"no_reset_password": True})
            self.uid_essai = usager.id

    def _menage(self, verifier=True):
        """Retirer ce que cet essai a VALIDÉ, et le vérifier.

        🔴 Ce que la vérification couvre, et ce qu'elle NE couvre pas. Elle
        compte les lignes des six tables nommées ci-dessous, pas tout l'état de
        la base. La première version ne regardait que quatre tables et se
        disait complète : on a mesuré qu'une marque
        `secure_transfer_brand` ACTIVE et des lignes `bf_dav_change`
        s'accumulaient à chaque passe, **six marques** sur une base d'essai. Elles ne
        viennent pas de l'assistant mais de la création d'un usager VALIDÉ :
        `bf_securetransfer` sème une marque par usager, et le miroir DAV
        enregistre son partenaire. Le reste du module n'a pas ce problème
        parce qu'aucun autre de ses 1 360 essais ne valide quoi que ce soit.

        ⚠️ Un essai qui valide dans une copie d'une base réelle doit donc
        nettoyer ce que les AUTRES modules ont écrit pour lui, pas seulement
        ses propres lignes. En ajouter un ici veut dire reprendre cette liste.
        """
        with self.registry.cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            cr.execute("DELETE FROM bf_email_account_setup WHERE email = %s",
                       (self.MARQUE,))
            # Les marques d'envoi sécurisé, semées par bf_securetransfer à la
            # création de l'usager. Leur `owner_user_id` passe à NULL quand
            # l'usager part (FK en SET NULL), donc elles survivent orphelines
            # ET ACTIVES : par nom, c'est le seul lien qui reste.
            # Sans bf_securetransfer (une installation publique, la CI), la
            # table n'existe pas : rien à nettoyer.
            if "secure.transfer.brand" in env:
                cr.execute("DELETE FROM secure_transfer_brand WHERE name LIKE %s",
                           ("%" + self.MARQUE + "%",))
            env["ir.mail_server"].sudo().search(
                [("from_filter", "=", self.MARQUE)]).unlink()
            env["bf.email.account"].sudo().search(
                [("login", "=", self.MARQUE)]).unlink()
            usagers = env["res.users"].sudo().with_context(
                active_test=False).search([("login", "=", self.MARQUE)])
            if usagers:
                # 🔴 Les règles semées AVANT l'usager, et pas par la cascade.
                # Mesuré sur une base d'essai : supprimer l'usager met `create_uid` à NULL
                # sur les conditions de règle (FK en SET NULL) tout en
                # emportant les règles elles-mêmes (FK en CASCADE), et l'UPDATE
                # revalide un `rule_id` déjà disparu —
                # « insert or update on table bf_email_rule_condition violates
                # foreign key constraint ». Hors portée ici, mais c'est
                # une vraie ornière pour qui supprime un usager qui a des
                # règles de boîte.
                env["bf.email.rule"].sudo().with_context(
                    active_test=False).search(
                        [("user_id", "in", usagers.ids)]).unlink()
                env["bf.email.identity"].sudo().with_context(
                    active_test=False).search(
                        [("user_id", "in", usagers.ids)]).unlink()
                partenaires = usagers.partner_id
                cles_dav = ["p%d" % pid for pid in partenaires.ids]
                usagers.unlink()
                partenaires.unlink()
                # Le miroir DAV garde une ligne par partenaire disparu.
                if cles_dav and "bf.dav.change" in env:
                    cr.execute(
                        "DELETE FROM bf_dav_change WHERE resource_key = ANY(%s)",
                        (cles_dav,))
            if verifier:
                for table, colonne in (("bf_email_account_setup", "email"),
                                       ("bf_email_account", "login"),
                                       ("res_users", "login"),
                                       ("res_partner", "email"),
                                       ("ir_mail_server", "from_filter")):
                    cr.execute(
                        "SELECT count(*) FROM %s WHERE %s = %%s"
                        % (table, colonne), (self.MARQUE,))
                    reste = cr.fetchone()[0]
                    if reste:
                        raise AssertionError(
                            "%s ligne(s) d'essai laissée(s) dans %s"
                            % (reste, table))
                # ⚠️ Un `assert` saute sous `python3 -O` : le contrôle du
                # ménage ne doit pas dépendre d'un drapeau.
                if "secure.transfer.brand" in env:
                    cr.execute(
                        "SELECT count(*) FROM secure_transfer_brand WHERE name LIKE %s",
                        ("%" + self.MARQUE + "%",))
                    reste = cr.fetchone()[0]
                    if reste:
                        raise AssertionError(
                            "%s marque(s) d'envoi sécurisé laissée(s) "
                            "par l'essai" % reste)
        self.registry.clear_all_caches()

    def _poser_la_ligne_validee(self):
        """Le `web_save` du client : la ligne est écrite ET VALIDÉE, dans sa
        propre transaction, avant que le bouton soit appuyé."""
        with self.registry.cursor() as cr:
            env = api.Environment(cr, self.uid_essai, {})
            # ⛔ Pas de `smtp_host` : avec lui, `_terminer` crée un
            # `ir.mail_server` ACTIF, et un essai qui valide en base en
            # laisserait un derrière lui si la passe s'arrêtait en plein vol.
            # La garde de la base d'essai refuse alors de démarrer Odoo (« serveur(s)
            # actif(s) ; neutraliser d'abord ») — mesuré. Le serveur sortant
            # n'est pas ce que cet essai mesure.
            ligne = env["bf.email.account.setup"].create({
                "email": self.MARQUE, "name": "Boîte validée",
                "state": "detecte",
                "imap_host": "imap.banc.invalid", "imap_port": 993,
                "password": self.SECRET,
            })
            return ligne.id

    def test_le_branchement_tient_sur_une_ligne_validee(self):
        setup_id = self._poser_la_ligne_validee()
        with self.registry.cursor() as cr:
            # 🔴 L'instantané de la transaction se prend ICI : en REPEATABLE
            # READ il est pris à la première requête, et une requête HTTP a
            # toujours déjà lu la session et l'usager quand elle arrive au
            # bouton. Sans cette lecture, le conflit ne se jouerait pas et
            # l'essai ne mesurerait rien.
            cr.execute("SELECT login FROM res_users WHERE id = %s",
                       (self.uid_essai,))
            self.assertTrue(cr.fetchone())
            env = api.Environment(cr, self.uid_essai, {})
            assistant = env["bf.email.account.setup"].browse(setup_id)
            self.assertEqual(assistant.password, self.SECRET)
            with patch.object(bf_email_imap, "open_connection",
                              lambda *a, **kw: FausseSession()), \
                 patch("odoo.addons.bf_email_management.models."
                       "bf_email_account.BfEmailAccount._semer_le_filigrane",
                       lambda self_, jours=7: True):
                try:
                    action = assistant.action_brancher()
                    env.flush_all()
                except psycopg2.Error as exc:
                    raise AssertionError(
                        "Le branchement a rendu %s — un curseur "
                        "séparé a validé au milieu de la transaction, et "
                        "`retrying` rejouerait sans le mot de passe. %s"
                        % (type(exc).__name__,
                           str(exc).strip().splitlines()[0])) from exc
            self.assertEqual(action.get("res_model"), "bf.email.account")
            compte = env["bf.email.account"].sudo().browse(action["res_id"])
            self.assertEqual(compte.password, self.SECRET,
                             "le compte n'a pas reçu le secret")
            self.assertEqual(assistant.state, "fini")
            # La transaction du bouton valide en sortant du gestionnaire.

        # Mesuré depuis une AUTRE connexion, sur ce que la table porte
        # vraiment : c'est la seule lecture qui prouve que le secret a quitté
        # la base, et non le cache de l'ORM.
        with self.registry.cursor() as cr:
            cr.execute("SELECT password, state FROM bf_email_account_setup "
                       "WHERE id = %s", (setup_id,))
            mot, etat = cr.fetchone()
            self.assertIsNone(
                mot, "le mot de passe est resté dans la table après la pose")
            self.assertEqual(etat, "fini")
            cr.execute("SELECT count(*), max(password) FROM bf_email_account "
                       "WHERE login = %s", (self.MARQUE,))
            nombre, secret = cr.fetchone()
            self.assertEqual(nombre, 1, "le compte n'a pas été validé en base")
            self.assertEqual(secret, self.SECRET)
