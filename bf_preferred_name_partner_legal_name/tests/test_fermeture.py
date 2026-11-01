# -*- coding: utf-8 -*-
"""Le « Legal name » de `partner_legal_name` fermé sur la fiche d'une personne, et les
valeurs déjà tapées déplacées à l'installation.
"""

from unittest.mock import patch

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged

from odoo.addons.bf_preferred_name_partner_legal_name.hooks import post_init_hook


@tagged("post_install", "-at_install", "bf_preferred_name")
class TestFermetureLegalName(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=False))
        cls.collegue = cls.env["res.users"].create({
            "name": "Camille Collègue",
            "login": "camille.pont@essai.test",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id,
                                  cls.env.ref("base.group_partner_manager").id])],
        })
        cls.personne = cls.env["res.partner"].create({"name": "Alex Personne"})
        cls.societe = cls.env["res.partner"].create({"name": "Société Essai", "is_company": True})
        cls.legal_field = cls.env["ir.model.fields"]._get("res.partner", "legal_name")
        cls.env.flush_all()
        cls.env.cr.precommit.run()

    def _vider_suivi(self):
        self.env.flush_all()
        self.env.cr.precommit.run()

    def _poser_en_sql(self, partner, legal_name):
        """Ce qu'une base d'avant le pont peut porter : le contrôle n'y était pas."""
        self.env.flush_all()
        self.env.cr.execute("UPDATE res_partner SET legal_name = %s WHERE id = %s",
                            [legal_name, partner.id])
        self.env.invalidate_all()

    def _personnes_avec_legal_name(self):
        Partner = self.env["res.partner"].with_context(active_test=False)
        return Partner.search([("legal_name", "!=", False), ("is_company", "=", False)]) \
            - self.env["res.company"].with_context(active_test=False).search([]).partner_id

    def test_refuse_sur_une_personne(self):
        Partner = self.env["res.partner"].with_user(self.collegue)
        with self.assertRaises(ValidationError):
            self.personne.with_user(self.collegue).write({"legal_name": "Nom Légal Tapé"})
        with self.assertRaises(ValidationError):
            Partner.create({"name": "Nouvelle Personne", "legal_name": "Nom Légal Tapé"})
        # L'import attrape l'erreur et la rend en message, sans rien créer.
        resultat = Partner.load(["name", "legal_name"], [["Importée", "Nom Légal Importé"]])
        self.assertFalse(resultat["ids"])
        self.assertTrue(resultat["messages"])
        # Une société en reste libre.
        self.societe.with_user(self.collegue).write({"legal_name": "Société Essai inc."})
        self.assertEqual(self.societe.legal_name, "Société Essai inc.")
        # Faire d'une société un particulier exige d'abord de vider ce nom.
        with self.assertRaises(ValidationError):
            self.societe.with_user(self.collegue).write({"is_company": False})

    def test_type_de_societe_vaut_case_societe(self):
        # « Type de société » seul
        # (import, API) faisait refuser une société comme une personne.
        Partner = self.env["res.partner"].with_user(self.collegue)
        creee = Partner.create({"name": "Soc API", "company_type": "company",
                                "legal_name": "Soc API inc."})
        self.assertTrue(creee.is_company)
        self.assertEqual(creee.legal_name, "Soc API inc.")
        resultat = Partner.load(["name", "company_type", "legal_name"],
                                [["Soc Importée", "company", "Soc Importée inc."]])
        self.assertFalse(resultat["messages"])
        importee = Partner.browse(resultat["ids"])
        self.assertTrue(importee.is_company)
        self.assertEqual(importee.legal_name, "Soc Importée inc.")
        self.personne.with_user(self.collegue).write(
            {"company_type": "company", "legal_name": "Alex Personne inc."})
        self.assertTrue(self.personne.is_company)
        # Le type gagne sur la case, comme son inverse : une personne reste fermée.
        with self.assertRaises(ValidationError):
            Partner.create({"name": "Pers API", "company_type": "person", "is_company": True,
                            "legal_name": "Pers API Légal"})
        resultat = Partner.load(["name", "company_type", "legal_name"],
                                [["Pers Importée", "person", "Pers Importée Légal"]])
        self.assertFalse(resultat["ids"])

    def test_permis_sur_la_fiche_d_une_societe_odoo(self):
        # L'en-tête des documents lit le nom légal sur la fiche de la société
        # Odoo, cochée « société » ou non.
        compagnie = self.env.company
        compagnie.partner_id.is_company = False
        compagnie.legal_name = "Compagnie Essai inc."
        self.env.flush_all()
        self.assertEqual(compagnie.partner_id.legal_name, "Compagnie Essai inc.")

    def test_installation_deplace_vers_le_nom_protege(self):
        # Une trace d'avant le pont : tapé quand la fiche était une société.
        self.personne.is_company = True
        self._vider_suivi()
        self.personne.legal_name = "Alexandra Légale"
        self._vider_suivi()
        self.env.cr.execute("UPDATE res_partner SET is_company = false WHERE id = %s",
                            [self.personne.id])
        self.env.invalidate_all()
        self.assertTrue(self.personne.message_ids.tracking_value_ids.filtered(
            lambda t: t.field_id == self.legal_field))
        deja = self.env["res.partner"].create({"name": "Lou Déjà"})
        deja.sudo().bf_legal_name = "Lou Protégé"
        self._poser_en_sql(deja, "Lou Autre")
        pareil = self.env["res.partner"].create({"name": "Max Pareil"})
        pareil.sudo().bf_legal_name = "Max Légal"
        self._poser_en_sql(pareil, "Max Légal")
        self.societe.legal_name = "Société Essai inc."
        self._vider_suivi()

        post_init_hook(self.env)
        self._vider_suivi()
        self.env.invalidate_all()

        self.assertEqual(self.personne.sudo().bf_legal_name, "Alexandra Légale")
        self.assertFalse(self.personne.legal_name)
        self.assertFalse(self.personne.message_ids.tracking_value_ids.filtered(
            lambda t: t.field_id == self.legal_field),
            "aucune trace du nom légal au fil, ni ancienne ni nouvelle")
        self.assertNotIn("Alexandra", " ".join(str(m.body) for m in self.personne.message_ids))
        # Jamais écrasé ; deux noms différents restent à trancher à la main.
        self.assertEqual(deja.sudo().bf_legal_name, "Lou Protégé")
        self.assertEqual(deja.legal_name, "Lou Autre")
        self.assertEqual(pareil.sudo().bf_legal_name, "Max Légal")
        self.assertFalse(pareil.legal_name)
        self.assertEqual(self.societe.legal_name, "Société Essai inc.")

    def test_installation_sans_effet_sans_personne(self):
        if self._personnes_avec_legal_name():
            self.skipTest("des personnes de cette base portent un « Legal name »")
        # Une société à nom légal, hors des sociétés Odoo : le hook n'y touche pas.
        # Sans elle, l'essai dépendait de la base (sur une base d'essai, seule la société Odoo
        # en porte un, et le hook la retire de toute façon).
        self.societe.legal_name = "Société Essai inc."
        self._vider_suivi()
        # Ni écriture de fiche, ni effacement au fil : le hook rend la main.
        interdit = AssertionError("le hook a écrit sur une base sans personne à déplacer")
        with patch.object(self.registry["res.partner"], "write", side_effect=interdit), \
                patch.object(self.registry["mail.tracking.value"], "unlink", side_effect=interdit), \
                patch.object(self.registry["mail.message"], "unlink", side_effect=interdit):
            post_init_hook(self.env)
