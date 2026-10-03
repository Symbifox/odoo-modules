"""Une fois sssd.conf écrit, la politique stagée perd le mot de passe de liaison."""
import importlib.util
import json
import os

from odoo.tests import TransactionCase, tagged

_CHEMIN = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "bfos_apply.py")
_MDP = "jeton-de-liaison-factice"


def _module():
    spec = importlib.util.spec_from_file_location("bfos_apply_redaction", _CHEMIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _politique():
    return {
        "schema": "bf-policy/v2",
        "user": {"login": "lia@l.example"},
        "install": {"hostname": "bf-lia", "login": {
            "mode": "sssd", "ldap_uri": "ldaps://ldap.l.example",
            "bind_dn": "cn=liaison,dc=l,dc=example", "bind_password": _MDP}},
        "policies": {"tpm_autounlock": {"enabled": True, "pcrs": "7"}},
    }


@tagged("post_install", "-at_install")
class TestApplyExpurge(TransactionCase):

    def _appliquer(self, politique, echec=()):
        mod = _module()
        ecrits = {}

        def writer(path, content, mode=None):
            if path in echec:
                raise OSError("disque plein")
            ecrits[path] = (content, mode)

        mod.apply(politique, root="/cible", run=lambda *a, **k: None, writer=writer)
        return mod, ecrits

    def test_mot_de_passe_retire_apres_sssd(self):
        mod, ecrits = self._appliquer(_politique())
        self.assertIn(_MDP, ecrits["/etc/sssd/sssd.conf"][0])
        contenu, mode = ecrits[mod.STAGED_JSON]
        self.assertEqual(mode, 0o600)
        self.assertNotIn(_MDP, contenu)
        stagee = json.loads(contenu)
        # Ce que relisent encore les outils de la machine est intact.
        self.assertEqual(stagee["user"]["login"], "lia@l.example")
        self.assertTrue(stagee["policies"]["tpm_autounlock"]["enabled"])
        self.assertEqual(stagee["install"]["login"]["ldap_uri"], "ldaps://ldap.l.example")

    def test_garde_si_sssd_a_echoue(self):
        mod, ecrits = self._appliquer(_politique(), echec=("/etc/sssd/sssd.conf",))
        self.assertNotIn(mod.STAGED_JSON, ecrits)

    def test_mode_local_sans_mot_de_passe_rien_a_reecrire(self):
        politique = _politique()
        politique["install"]["login"] = {"mode": "local"}
        mod, ecrits = self._appliquer(politique)
        self.assertNotIn(mod.STAGED_JSON, ecrits)
