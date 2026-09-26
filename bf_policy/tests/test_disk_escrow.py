"""Sequestre de la phrase de passe du disque.

Ce qui est couvert ici, et pourquoi :

- **Le fail-closed.** Sans cle de chiffrement configuree, le depot doit ECHOUER
  et le dire. C'est le test le plus important du fichier : si le sequestre
  acceptait la phrase pour la jeter, l'installateur croirait pouvoir s'en servir
  et fermerait un disque avec une phrase que personne ne possede.
- **Rien en clair dans la base.** La colonne ne contient jamais la phrase.
- **L'omission n'efface pas.** Un ré-enrolement sans phrase (machine qui rejoue
  son install, appel d'un client plus ancien) ne doit pas vider un depot
  existant : un effacement silencieux serait pire qu'une absence visible.
- **La separation des roles.** Administrer Odoo ne donne PAS le droit de lire un
  sequestre. C'est toute la raison d'etre du groupe dedie.
- **L'audit ne se contourne pas.** La trace est ecrite avant que la valeur ne
  sorte, et elle nomme qui a lu.

Le chemin HTTP d'enrolement n'est pas rejouable sans IdP (cf. test_machine_enrol) :
la logique de depot est donc exercee au niveau modele, la ou elle vit.
"""

import os
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.bf_policy.models import escrow

_PHRASE = "PWSND-4KX7M-Q2RTB-9HJVC-ZE6YA"


def _a_key():
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode()


class EscrowCase(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({
            "name": "Foxy Inc.", "website": "https://foxy.example",
        })
        cls.org = cls.env["bf.policy.org"].create({
            "company_id": cls.company.id, "domain": "foxy.example",
            "disk_escrow": True,
        })
        cls.user = cls.env["res.users"].create({
            "name": "Riley", "login": "riley@foxy.example",
            "company_id": cls.company.id, "company_ids": [(4, cls.company.id)],
        })
        cls.key = _a_key()

    def _machine(self, uuid="mach-0001-aaaa"):
        machine, _token = self.env["bf.policy.machine"]._enrol(
            self.org, self.user, machine_uuid=uuid, hostname="bf-riley")
        return machine

    def _with_key(self, key=None):
        """Pose la cle dans l'environnement, comme le ferait le conteneur."""
        return patch.dict(os.environ,
                          {escrow.ENV_VAR: key if key is not None else self.key})

    def _admin_without_the_right(self):
        """Quelqu'un qui administre Odoo, et rien de plus.

        Vit sur la classe de base parce que deux classes de tests en ont besoin
        pour exercer le sequestre : celle qui garde la separation des roles, et
        celle qui garde le non-stockage de la phrase.
        """
        return self.env["res.users"].create({
            "name": "Admin sans droit", "login": "admin-sans-droit@foxy.example",
            "groups_id": [(6, 0, [self.env.ref("base.group_system").id])],
        })

    def _without_key(self):
        """Retire toute source de cle, y compris odoo.conf.

        ⚠️ On patche l'INSTANCE, pas le chemin pointe « odoo.tools.config.get ».
        `odoo.tools.config` designe deux choses selon qui resout : le sous-module
        `odoo/tools/config.py` pour importlib, et le configmanager pour l'import
        `from odoo.tools import config`. mock.patch prend le premier, qui n'a pas
        de `get` — d'ou un AttributeError qui ressemble a un bogue du code teste
        alors qu'il ne vient que de la cible du patch.
        """
        from odoo.tools import config as odoo_config
        env = patch.dict(os.environ, {escrow.ENV_VAR: ""})
        cfg = patch.object(odoo_config, "get", return_value="")
        return env, cfg


@tagged("post_install", "-at_install")
class TestEscrowFailsClosed(EscrowCase):
    def test_no_key_refuses_and_says_why(self):
        machine = self._machine()
        env_patch, cfg_patch = self._without_key()
        with env_patch, cfg_patch:
            ok, reason = machine._escrow_disk_passphrase(_PHRASE)
        self.assertFalse(ok, "sans cle, le depot doit echouer")
        self.assertIn("cle de sequestre", reason)
        self.assertFalse(machine.disk_escrowed_on)
        self.assertFalse(machine.sudo().disk_passphrase_enc)

    def test_malformed_key_refuses(self):
        machine = self._machine()
        with self._with_key("pas-une-cle-fernet"):
            ok, reason = machine._escrow_disk_passphrase(_PHRASE)
        self.assertFalse(ok)
        self.assertIn("invalide", reason)

    def test_available_reports_state_without_raising(self):
        env_patch, cfg_patch = self._without_key()
        with env_patch, cfg_patch:
            self.assertFalse(escrow.available())
            self.assertTrue(escrow.unavailable_reason())
        with self._with_key():
            self.assertTrue(escrow.available())
            self.assertEqual(escrow.unavailable_reason(), "")


@tagged("post_install", "-at_install")
class TestEscrowStorage(EscrowCase):
    def test_stores_encrypted_and_round_trips(self):
        machine = self._machine()
        with self._with_key():
            ok, reason = machine._escrow_disk_passphrase(_PHRASE)
            self.assertTrue(ok, reason)
            stored = machine.sudo().disk_passphrase_enc
            self.assertTrue(stored)
            self.assertNotIn(_PHRASE, stored,
                             "la phrase ne doit jamais apparaitre en clair")
            self.assertEqual(machine._read_disk_passphrase(), _PHRASE)
        self.assertTrue(machine.disk_escrowed_on)
        self.assertTrue(machine.disk_escrowed)

    def test_wrong_key_cannot_read_back(self):
        """Une cle perdue ou changee = ce depot est perdu. On veut que ca leve."""
        machine = self._machine()
        with self._with_key():
            machine._escrow_disk_passphrase(_PHRASE)
        with self._with_key(_a_key()):
            with self.assertRaises(Exception):
                machine._read_disk_passphrase()

    def test_empty_passphrase_does_not_wipe_an_existing_deposit(self):
        machine = self._machine()
        with self._with_key():
            machine._escrow_disk_passphrase(_PHRASE)
            before = machine.sudo().disk_passphrase_enc
            ok, _reason = machine._escrow_disk_passphrase("")
            self.assertFalse(ok)
            self.assertEqual(machine.sudo().disk_passphrase_enc, before)
            self.assertTrue(machine.disk_escrowed)

    def test_rejects_implausible_passphrases(self):
        Machine = self.env["bf.policy.machine"]
        self.assertFalse(Machine._valid_passphrase("court"))
        self.assertFalse(Machine._valid_passphrase("x" * 257))
        self.assertFalse(Machine._valid_passphrase("avec\nsaut"))
        self.assertFalse(Machine._valid_passphrase("accentué-quand-même"))
        self.assertFalse(Machine._valid_passphrase(None))
        self.assertTrue(Machine._valid_passphrase(_PHRASE))

    def test_a_second_deposit_never_replaces_the_first(self):
        """Un depot en place n'est jamais ecrase, pas meme par son proprietaire.

        L'installateur tire un UUID neuf a chaque installation : seul un
        enrolement rejoue a la main tombe ici, et c'est alors l'ANCIENNE phrase
        qui ouvre le disque. Le refus fait retomber l'installateur sur la saisie
        manuelle ; remplacer un depot passe par « Retirer le sequestre ».
        """
        machine = self._machine()
        with self._with_key():
            machine._escrow_disk_passphrase(_PHRASE)
            ok, reason = machine._escrow_disk_passphrase(
                "AAAAA-BBBBB-CCCCC-DDDDD-EEEEE")
            self.assertFalse(ok)
            self.assertIn("deja en depot", reason)
            self.assertEqual(machine._read_disk_passphrase(), _PHRASE)


@tagged("post_install", "-at_install")
class TestEscrowAuthorization(EscrowCase):
    def test_system_admin_alone_cannot_reveal(self):
        """Le coeur du dispositif : administrer Odoo n'ouvre pas les disques."""
        machine = self._machine()
        with self._with_key():
            machine._escrow_disk_passphrase(_PHRASE)
        admin = self._admin_without_the_right()
        self.assertFalse(
            admin.has_group("bf_policy.group_disk_escrow_read"),
            "base.group_system ne doit PAS impliquer le droit de sequestre")
        with self._with_key(), self.assertRaises(AccessError):
            machine.with_user(admin).action_reveal_passphrase()

    def test_holder_reveals_and_the_read_is_recorded(self):
        machine = self._machine()
        with self._with_key():
            machine._escrow_disk_passphrase(_PHRASE)
        holder = self._admin_without_the_right()
        holder.write({"groups_id": [
            (4, self.env.ref("bf_policy.group_disk_escrow_read").id)]})

        with self._with_key():
            action = machine.with_user(holder).action_reveal_passphrase()
            # La phrase est CALCULEE a l'affichage : elle se lit donc dans le
            # contexte ou la cle existe, comme le fera le serveur qui peint
            # l'ecran. C'est le prix a payer pour qu'elle ne soit jamais ecrite
            # dans la table du wizard.
            wizard = self.env["bf.policy.machine.reveal"].with_user(
                holder).browse(action["res_id"])
            self.assertEqual(wizard.passphrase, _PHRASE)

        machine.invalidate_recordset()
        self.assertEqual(machine.disk_reveal_count, 1)
        self.assertEqual(machine.disk_last_revealed_by, holder)
        self.assertTrue(machine.disk_last_revealed_on)

    def test_reveal_without_deposit_explains_itself(self):
        machine = self._machine()
        holder = self._admin_without_the_right()
        holder.write({"groups_id": [
            (4, self.env.ref("bf_policy.group_disk_escrow_read").id)]})
        with self._with_key(), self.assertRaises(UserError):
            machine.with_user(holder).action_reveal_passphrase()

    def test_forget_clears_the_deposit(self):
        machine = self._machine()
        with self._with_key():
            machine._escrow_disk_passphrase(_PHRASE)
        holder = self._admin_without_the_right()
        holder.write({"groups_id": [
            (4, self.env.ref("bf_policy.group_disk_escrow_read").id)]})
        machine.with_user(holder).action_forget_passphrase()
        machine.invalidate_recordset()
        self.assertFalse(machine.disk_escrowed)
        self.assertFalse(machine.sudo().disk_passphrase_enc)


@tagged("post_install", "-at_install")
class TestEscrowInPolicyJson(EscrowCase):
    def test_policy_advertises_the_setting(self):
        with self._with_key():
            policy = self.org.get_policy_json(self.user)
        block = policy["policies"]["disk_escrow"]
        self.assertTrue(block["enabled"])
        self.assertTrue(block["available"])

    def test_unavailable_when_no_key_even_if_enabled(self):
        """L'installateur doit pouvoir distinguer « pas voulu » de « pas branche »."""
        env_patch, cfg_patch = self._without_key()
        with env_patch, cfg_patch:
            policy = self.org.get_policy_json(self.user)
        block = policy["policies"]["disk_escrow"]
        self.assertTrue(block["enabled"])
        self.assertFalse(block["available"])

    def test_disabled_org_says_so(self):
        self.org.disk_escrow = False
        with self._with_key():
            policy = self.org.get_policy_json(self.user)
        self.assertFalse(policy["policies"]["disk_escrow"]["enabled"])

    def test_the_wizard_table_never_holds_the_passphrase(self):
        """Le defaut garde ici : un TransientModel a une vraie table.

        Un `fields.Char` ordinaire y aurait ecrit la phrase en clair, et le
        menage automatique (cron quotidien, age minimum 1 h) l'y aurait laissee
        jusqu'au `pg_dump` de la nuit, pousse hors site. On interroge donc la
        base directement : le seul endroit qui compte.
        """
        machine = self._machine()
        with self._with_key():
            machine._escrow_disk_passphrase(_PHRASE)
        holder = self._admin_without_the_right()
        holder.write({"groups_id": [
            (4, self.env.ref("bf_policy.group_disk_escrow_read").id)]})

        with self._with_key():
            machine.with_user(holder).action_reveal_passphrase()

        self.env.cr.execute("""
            SELECT column_name FROM information_schema.columns
             WHERE table_name = 'bf_policy_machine_reveal'
        """)
        columns = {row[0] for row in self.env.cr.fetchall()}
        self.assertNotIn(
            "passphrase", columns,
            "la phrase de passe aurait une colonne PostgreSQL : elle partirait "
            "en clair dans la sauvegarde hors site")

    def test_losing_the_right_blanks_an_already_open_reveal_screen(self):
        """L'id d'un wizard transitoire est devinable, et le droit se retire."""
        machine = self._machine()
        with self._with_key():
            machine._escrow_disk_passphrase(_PHRASE)
        holder = self._admin_without_the_right()
        group = self.env.ref("bf_policy.group_disk_escrow_read")
        holder.write({"groups_id": [(4, group.id)]})

        with self._with_key():
            action = machine.with_user(holder).action_reveal_passphrase()
            holder.write({"groups_id": [(3, group.id)]})
            wizard = self.env["bf.policy.machine.reveal"].with_user(
                holder).browse(action["res_id"])
            wizard.invalidate_recordset()
            self.assertFalse(wizard.passphrase)
