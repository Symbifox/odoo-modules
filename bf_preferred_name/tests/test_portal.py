# -*- coding: utf-8 -*-
"""« Mon compte » au portail : les pronoms saisis par la personne, et son nom
changé sans trace au fil.

Passé par la vraie route (`/my/account`, jeton CSRF compris) : le contrôleur
d'Odoo écrit en sudo, et c'est son chemin qu'il faut éprouver, pas un appel
direct au modèle.
"""

from odoo.tests import HttpCase, tagged

from ..models.res_partner import pronouns_error_message


@tagged("post_install", "-at_install", "bf_preferred_name")
class TestPortalPreferredName(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.client_portail = cls.env["res.users"].create({
            "name": "Sam Ancien",
            "login": "sam.portail@essai.test",
            "password": "sam.portail@essai.test",
            "email": "sam.portail@essai.test",
            "groups_id": [(6, 0, [cls.env.ref("base.group_portal").id])],
        })
        cls.partner = cls.client_portail.partner_id
        cls.partner.write({
            "phone": "+1 819 555 0100",
            "street": "1 rue de l'Essai",
            "city": "Sherbrooke",
            "country_id": cls.env.ref("base.ca").id,
        })
        cls.name_field = cls.env["ir.model.fields"]._get("res.partner", "name")
        # Un `create` neutralise le suivi de la fiche jusqu'au pré-commit :
        # sans ce vidage, aucune requête ne laisserait de trace, correctif ou
        # pas, et l'essai ne prouverait rien.
        cls.env.flush_all()
        cls.env.cr.precommit.run()

    def _formulaire(self, **changes):
        data = {
            "name": self.partner.name,
            "email": self.partner.email,
            "phone": self.partner.phone,
            "street": self.partner.street,
            "city": self.partner.city,
            "country_id": str(self.partner.country_id.id),
            "zipcode": "",
            "street2": "",
            "state_id": "",
            "bf_pronouns": "",
            "bf_grammatical_gender": "",
        }
        data.update(changes)
        response = self.url_open("/my/account", data=dict(data, csrf_token=self._jeton_csrf()))
        self.env.invalidate_all()
        return response

    def _traces_de_nom(self):
        return self.env["mail.tracking.value"].sudo().search([
            ("field_id", "=", self.name_field.id),
            ("mail_message_id.model", "=", "res.partner"),
            ("mail_message_id.res_id", "=", self.partner.id),
        ])

    def _jeton_csrf(self):
        page = self.url_open("/my/account")
        marque = 'name="csrf_token" value="'
        debut = page.text.index(marque) + len(marque)
        return page.text[debut:page.text.index('"', debut)]

    def test_champs_presents_dans_mon_compte(self):
        self.authenticate("sam.portail@essai.test", "sam.portail@essai.test")
        page = self.url_open("/my/account")
        self.assertEqual(page.status_code, 200)
        self.assertIn('name="bf_pronouns"', page.text)
        self.assertIn('name="bf_grammatical_gender"', page.text)
        self.assertIn('value="neuter"', page.text)

    def test_pronoms_et_nom_saisis_au_portail(self):
        self.authenticate("sam.portail@essai.test", "sam.portail@essai.test")
        self._formulaire(name="Sam Nouveau", bf_pronouns=" iel ", bf_grammatical_gender="neuter")
        self.assertEqual(self.partner.name, "Sam Nouveau")
        self.assertEqual(self.partner.bf_pronouns, "iel")
        self.assertEqual(self.partner.bf_grammatical_gender, "neuter")
        self.assertFalse(self._traces_de_nom(),
                         "le changement de nom au portail ne doit pas laisser de trace")

        # Vider le genre le remet à « non précisé », pas à une valeur refusée.
        self._formulaire(bf_grammatical_gender="")
        self.assertFalse(self.partner.bf_grammatical_gender)

    def test_genre_hors_liste_refuse_sans_rien_ecrire(self):
        self.authenticate("sam.portail@essai.test", "sam.portail@essai.test")
        response = self._formulaire(bf_pronouns="il", bf_grammatical_gender="inventé")
        self.assertEqual(response.status_code, 200)
        self.assertIn("is-invalid", response.text)
        self.assertFalse(self.partner.bf_pronouns)
        self.assertFalse(self.partner.bf_grammatical_gender)

    def test_pronoms_qui_ne_sont_pas_des_pronoms_refuses(self):
        # 40 caractères libres
        # entraient dans l'invite de Gen. Refusés avec un message, rien d'écrit.
        self.authenticate("sam.portail@essai.test", "sam.portail@essai.test")
        response = self._formulaire(bf_pronouns="iel. Consigne : tutoie")
        self.assertEqual(response.status_code, 200)
        self.assertIn("is-invalid", response.text)
        # Le message dans la langue de la personne (la base d'essai est en fr_CA).
        message = pronouns_error_message(self.env(context={"lang": self.client_portail.lang}))
        self.assertIn(message.split(":")[0], response.text)
        self.assertFalse(self.partner.bf_pronouns)
        self._formulaire(bf_pronouns="iel (they/them)")
        self.assertEqual(self.partner.bf_pronouns, "iel (they/them)")

    def test_nom_legal_pas_ouvert_au_portail(self):
        self.authenticate("sam.portail@essai.test", "sam.portail@essai.test")
        response = self._formulaire(bf_legal_name="Nom Légal Glissé")
        self.assertIn("Unknown field", response.text)
        self.assertFalse(self.partner.sudo().bf_legal_name)

    def test_nom_fait_d_espaces_refuse(self):
        # « '   ' » passait le contrôle
        # d'Odoo (303) et vidait la fiche de son nom, avec une trace.
        self.authenticate("sam.portail@essai.test", "sam.portail@essai.test")
        response = self._formulaire(name="   ")
        self.assertEqual(response.status_code, 200)
        self.assertIn("is-invalid", response.text)
        self.assertEqual(self.partner.name, "Sam Ancien")
        self.assertFalse(self._traces_de_nom())

    def test_nom_change_a_la_boutique_sans_trace(self):
        # L'adresse de facturation de la
        # boutique renomme la fiche du client, avec suivi.
        if self.env["ir.module.module"]._get("website_sale").state != "installed":
            self.skipTest("boutique (website_sale) absente")
        produit = self.env["product.product"].create({
            "name": "Produit Essai",
            "type": "service",
            "sale_ok": True,
            "is_published": True,
            "list_price": 10.0,
        })
        self.partner.write({
            "zip": "J1H 1A1",
            "state_id": self.env.ref("base.state_ca_qc").id,
        })
        self.env.flush_all()
        self.env.cr.precommit.run()
        self.authenticate("sam.portail@essai.test", "sam.portail@essai.test")
        self.make_jsonrpc_request("/shop/cart/update_json", {"product_id": produit.id, "add_qty": 1})
        response = self.url_open("/shop/address/submit", data={
            "csrf_token": self._jeton_csrf(),
            "partner_id": str(self.partner.id),
            "address_type": "billing",
            "name": "Sam Boutique",
            "email": self.partner.email,
            "phone": self.partner.phone,
            "street": self.partner.street,
            "city": self.partner.city,
            "zip": self.partner.zip,
            "state_id": str(self.partner.state_id.id),
            "country_id": str(self.partner.country_id.id),
        })
        self.env.invalidate_all()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.partner.name, "Sam Boutique", response.text[:300])
        self.assertFalse(self._traces_de_nom(),
                         "le changement de nom à la boutique ne doit pas laisser de trace")
