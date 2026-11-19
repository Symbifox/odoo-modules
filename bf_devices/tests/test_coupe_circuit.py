"""Révoquer un appareil doit SONNER avant d'effacer son endpoint.

Volet 2 du lot. La page « Mes appareils » retire l'accès en mettant
`active` à faux et en effaçant `push_endpoint` (deux des trois sources le
listent dans `efface`). Un appareil sans endpoint ne se réveille plus : si la
sonnerie partait après l'écriture, elle ne partirait jamais.
"""
from unittest.mock import patch

from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestSonnerieALaRevocation(TransactionCase):
    """⚠️ `post_install` n'est pas décoratif : `bf_devices` ne dépend d'aucun
    module d'appariement, donc il se charge AVANT eux. Au moment où ses essais
    d'installation tournent, `sms.archive.mobile.device` n'est pas encore au
    registre et le montage lève un `KeyError`."""

    def setUp(self):
        super().setUp()
        # ⚠️ Cette classe éprouve la SONNERIE, qui n'existe que pour une source
        # portant un `push_endpoint`. `bf_devices` ne dépend d'aucun module
        # d'appariement : chez qui ne les a pas, il n'y a rien à mesurer ici.
        if "sms.archive.mobile.device" not in self.env:
            self.skipTest("aucune source à endpoint sur cette base")
        self.Registre = self.env["bf.device.registry"]
        self.personne = self.env["res.users"].create({
            "name": "Révocante", "login": "banc-devices-cc",
            "groups_id": [(4, self.env.ref("base.group_user").id)],
        })
        self.appareil = self.env["sms.archive.mobile.device"].sudo().create({
            "user_id": self.personne.id, "name": "Téléphone",
            "push_endpoint": "https://ntfy.example.com/abc",
        })
        self.cle = "sms.archive.mobile.device,%s" % self.appareil.id

    def _revoquer_en_observant(self):
        """Rend la liste des (endpoint vu par la sonnerie, payload)."""
        vus = []
        Transport = type(self.env["sms.archive.unifiedpush"])
        vrai = Transport._reveiller

        def espion(soi, appareils):
            # 🔴 On lit l'endpoint AU MOMENT de la sonnerie, pas après : c'est
            # tout l'objet de l'essai.
            vus.append([(a.id, a.push_endpoint, a.active) for a in appareils])
            return vrai(soi, appareils)

        with patch.object(Transport, "_reveiller", espion), \
                patch.object(Transport, "_envoyer_a", lambda s, a, p: None):
            self.Registre.with_user(self.personne)._revoquer(
                self.cle, usager=self.personne)
        return vus

    def test_la_sonnerie_part_avant_l_effacement_de_l_endpoint(self):
        vus = self._revoquer_en_observant()
        self.assertEqual(len(vus), 1, "la révocation doit sonner une fois")
        (identifiant, endpoint, actif), = vus[0]
        self.assertEqual(identifiant, self.appareil.id)
        self.assertTrue(
            endpoint,
            "sonner APRÈS l'effacement revient à ne jamais sonner : l'appareil "
            "n'a plus d'endpoint par où être joint")
        self.assertTrue(actif, "l'appareil est encore joignable quand on sonne")

    def test_la_revocation_fait_son_travail_malgre_tout(self):
        self._revoquer_en_observant()
        self.assertFalse(self.appareil.sudo().active)
        self.assertFalse(self.appareil.sudo().push_endpoint)

    def test_une_sonnette_cassee_ne_bloque_pas_la_revocation(self):
        """🔴 C'est l'écriture qui coupe, pas le réveil."""
        Transport = type(self.env["sms.archive.unifiedpush"])

        def boum(soi, appareils):
            raise RuntimeError("ntfy injoignable")

        with patch.object(Transport, "_reveiller", boum):
            self.assertTrue(self.Registre.with_user(self.personne)._revoquer(
                self.cle, usager=self.personne))
        self.assertFalse(self.appareil.sudo().active)

    def test_une_source_sans_transport_ne_fait_pas_tomber_la_sonnerie(self):
        """Le coffre de tokens n'a aucun push : il n'a pas de sonnette, et
        c'est le 401 de son prochain appel qui le coupera."""
        if "bf.otp.device" not in self.env:
            self.skipTest("bf_otp absent de ce banc")
        appareil = self.env["bf.otp.device"].sudo().create({
            "user_id": self.personne.id, "name": "Coffre"})
        self.assertFalse(self.Registre._sonner(appareil))

    def test_revoquer_tout_sonne_chez_chaque_appareil(self):
        second = self.env["sms.archive.mobile.device"].sudo().create({
            "user_id": self.personne.id, "name": "Tablette",
            "push_endpoint": "https://ntfy.example.com/def",
        })
        sonnes = []
        Transport = type(self.env["sms.archive.unifiedpush"])
        with patch.object(Transport, "_reveiller",
                          lambda s, a: sonnes.extend(a.ids)), \
                patch.object(Transport, "_envoyer_a", lambda s, a, p: None):
            self.Registre.with_user(self.personne)._revoquer_tout(
                usager=self.personne)
        self.assertEqual(sorted(sonnes),
                         sorted(self.appareil.ids + second.ids))
