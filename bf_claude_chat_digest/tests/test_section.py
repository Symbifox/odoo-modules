"""La section du digest, et surtout ce qu'elle refuse de faire passer pour calme.

⚠️ Le destinataire des essais est une personne RÉELLE, jamais `self.env.user` :
sur un banc, c'est le superutilisateur, et la section se lit au nom du
destinataire.
"""

from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestSectionConsommation(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = cls.env["daily.digest.config"].create({"name": "Digest de banc"})
        cls.admin = new_test_user(
            cls.env, login="digest-admin", groups="base.group_user,base.group_system")
        cls.simple = new_test_user(
            cls.env, login="digest-simple", groups="base.group_user")
        # Les comptes semés par d'autres essais ou par la production copiée ne
        # doivent pas brouiller les lectures.
        cls.env["claude.account"].search([]).write({"active": False})

    def _compte(self, nom="Compte d'essai", charge=None):
        Compte = self.env["claude.account"]
        compte_id = Compte.enregistrer_releve(
            f"/tmp/claude-{nom}", nom=nom, charge=charge or {
                "five_hour": {"utilization": 3.0, "resets_at":
                              (fields.Datetime.now() + timedelta(hours=2)).isoformat() + "Z"},
                "seven_day": {"utilization": 55.0, "resets_at":
                              (fields.Datetime.now() + timedelta(days=2)).isoformat() + "Z"},
            })
        return Compte.browse(compte_id)

    def _section(self, user=None):
        return self.config._render_claude_usage_section(user or self.admin)

    def test_les_compteurs_paraissent_meme_un_jour_calme(self):
        """C'est un compteur qu'on veut lire chaque matin, pas une alerte."""
        self._compte("Compte principal")
        section = self._section()
        self.assertIn("Consommation Claude", section)
        self.assertIn("Compte principal", section)
        self.assertIn("55\u00a0%", section)
        self.assertIn("Sous les seuils", section)
        self.assertIn("bascule", section)

    def test_sans_compte_pas_de_section(self):
        """Un locataire où la sonde ne verse pas n'a rien à dire."""
        self.assertEqual(self._section(), "")

    def test_un_destinataire_non_admin_ne_voit_rien(self):
        """🔴 Le digest part d'un superutilisateur ; la lecture se fait au nom
        du destinataire, sinon la consommation fuit à tout le monde."""
        self._compte("Compte principal")
        self.assertEqual(self._section(self.simple), "")

    def test_la_case_a_cocher_fait_taire_la_section(self):
        self._compte("Compte principal")
        self.config.include_claude_usage = False
        self.assertEqual(self._section(), "")

    def test_un_releve_perime_passe_en_rouge(self):
        """Un compteur figé garde ses fenêtres et `_juger` le dit encore sous
        les seuils. La section ne doit pas le faire passer pour rassurant."""
        compte = self._compte("Compte principal")
        compte.dernier_releve = fields.Datetime.now() - timedelta(hours=9)
        section = self._section()
        self.assertIn("Aucun relevé depuis 9 h", section)
        self.assertIn("Attention : un compte n&#39;a pas de relevé à jour", section)
        self.assertNotIn("Sous les seuils", section)

    def test_une_sonde_en_erreur_dit_son_diagnostic(self):
        compte = self._compte("Compte secondaire")
        self.env["claude.account"].enregistrer_releve(
            compte.config_dir, erreur="HTTP 401 : jeton périmé")
        section = self._section()
        self.assertIn("jeton périmé", section)
        self.assertIn("Attention", section)

    def test_une_bascule_passee_ne_montre_pas_le_chiffre_comme_actuel(self):
        self._compte("Compte principal", charge={
            "five_hour": {"utilization": 97.0, "resets_at":
                          (fields.Datetime.now() - timedelta(hours=1)).isoformat() + "Z"},
        })
        self.assertIn("bascule passée", self._section())

    def test_les_noms_ne_sont_pas_doublement_echappes(self):
        self._compte("R&D")
        section = self._section()
        self.assertIn("R&amp;D", section)
        self.assertNotIn("&amp;amp;", section)

    def test_la_section_porte_sa_propre_ligne(self):
        """⚠️ Le marqueur « Divider » est ENTRE deux <tr>. Une section nue y
        serait sortie de la table par l'analyseur HTML."""
        enveloppe = "<table><tr><td>haut</td></tr><!-- Divider --><tr><td>bas</td></tr></table>"
        sortie = self.config._splice_claude_usage_section(enveloppe, "<h3>Claude</h3>")
        self.assertIn('<tr><td style="padding:0 24px 24px 24px;"><h3>Claude</h3></td></tr>'
                      "<!-- Divider -->", sortie)
        self.assertEqual(
            self.config._splice_claude_usage_section("<p>x</p>", "<h3>y</h3>"), "<p>x</p>")
