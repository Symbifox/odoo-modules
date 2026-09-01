# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Un test par trou trouvé à l'audit adversarial du 2026-08-31.

Chacun échouait avant son correctif. Ils sont ici pour que le trou ne puisse
pas se rouvrir sans que quelqu'un s'en aperçoive.
"""

import json
import uuid

from odoo.exceptions import AccessError
from odoo.tests import HttpCase, TransactionCase, tagged

from ..models.bf_patch_report import MIRRORED_FIELDS

BASE = "/symbifox/patch/v1"


@tagged("post_install", "-at_install")
class TestAuditModele(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({
            "name": "Client d'audit", "is_company": True,
        })
        cls.endpoint = cls.env["hosting.endpoint"].create({
            "name": "audit-01", "partner_id": cls.partner.id,
        })
        cls.system = cls.env["bf.patch.system"].create({
            "name": "audit-01-linux", "endpoint_id": cls.endpoint.id,
            "machine_id": uuid.uuid4().hex, "patch_managed": True,
        })

    def test_un_compte_inconnu_ne_passe_jamais_au_vert(self):
        """🔴 Le trou central : un gestionnaire de paquets cassé rend une sortie
        vide, donc zéro paquet, donc une machine verte. C'est exactement la
        panne que le module existe pour éliminer."""
        self.system._apply_report({
            "pending_known": False, "pending_count": 0, "packages": [],
        })
        self.assertEqual(self.system.patch_state, "blind")
        self.assertEqual(self.endpoint.patch_state, "blind",
                         "la machine ne doit pas verdir sur un compte inconnu")

    def test_un_vrai_zero_reste_vert(self):
        self.system._apply_report({
            "pending_known": True, "pending_count": 0, "packages": [],
        })
        self.assertEqual(self.system.patch_state, "ok")

    def test_le_releve_n_ecrase_pas_les_champs_d_audit_de_la_fiche(self):
        """La recopie « tout champ commun aux deux modèles » emportait
        create_uid, create_date, write_uid et display_name."""
        self.assertNotIn("create_uid", MIRRORED_FIELDS)
        self.assertNotIn("write_uid", MIRRORED_FIELDS)
        self.assertNotIn("display_name", MIRRORED_FIELDS)

        auteur = self.system.create_uid
        cree_le = self.system.create_date
        self.system._apply_report({"pending_count": 3, "pending_known": True})
        self.system.invalidate_recordset()
        self.assertEqual(self.system.create_uid, auteur)
        self.assertEqual(self.system.create_date, cree_le)

    def test_poser_un_agent_exige_le_gestionnaire(self):
        """Les deux actions n'ont pas de préfixe `_` : elles sont appelables par
        RPC par tout utilisateur ayant accès au modèle."""
        simple = self.env["res.users"].create({
            "name": "Utilisateur hébergement", "login": f"heb-{uuid.uuid4().hex[:8]}",
            "groups_id": [(6, 0, [
                self.env.ref("base.group_user").id,
                self.env.ref("hosting_management.group_hosting_user").id,
            ])],
        })
        with self.assertRaises(AccessError):
            self.endpoint.with_user(simple).action_generate_enrol_code()
        with self.assertRaises(AccessError):
            self.system.with_user(simple).action_revoke_agent()

    def test_les_releves_portent_une_regle_d_enregistrement(self):
        """Le modèle enfant n'hérite pas de la règle du parent : sans règle
        propre, un utilisateur borné à ses clients lisait les relevés de tout
        le parc, charge utile comprise."""
        for model in ("bf.patch.system", "bf.patch.report",
                      "bf.patch.package"):
            rules = self.env["ir.rule"].search([("model_id.model", "=", model)])
            self.assertTrue(rules, f"{model} n'a aucune règle d'enregistrement")

    def test_un_utilisateur_ne_lit_pas_les_releves_d_un_autre_client(self):
        """La preuve, pas la présomption : vérifier qu'une règle EXISTE ne dit
        pas qu'elle mord. On lit pour de vrai, sous l'identité d'un utilisateur
        borné à un autre client.

        ⚠️ Le cache de l'ORM est par transaction : une lecture en `sudo` avant
        celle-ci ferait passer le test pour la mauvaise raison. D'où
        l'invalidation.
        """
        etranger = self.env["res.partner"].create({
            "name": "Client étranger", "is_company": True,
        })
        endpoint_etranger = self.env["hosting.endpoint"].create({
            "name": "pas-a-lui", "partner_id": etranger.id,
        })
        systeme_etranger = self.env["bf.patch.system"].create({
            "name": "pas-a-lui-linux", "endpoint_id": endpoint_etranger.id,
            "machine_id": uuid.uuid4().hex, "patch_managed": True,
        })
        systeme_etranger._apply_report({
            "pending_count": 42, "pending_known": True,
            "packages": [{"name": "secret-interne"}],
        })

        curieux = self.env["res.users"].create({
            "name": "Utilisateur curieux",
            "login": f"curieux-{uuid.uuid4().hex[:8]}",
            "groups_id": [(6, 0, [
                self.env.ref("base.group_user").id,
                self.env.ref("hosting_management.group_hosting_user").id,
            ])],
        })

        self.env.invalidate_all()
        vus = self.env["bf.patch.report"].with_user(curieux).search(
            [("endpoint_id", "=", endpoint_etranger.id)]
        )
        self.assertFalse(
            self.env["bf.patch.system"].with_user(curieux).search(
                [("endpoint_id", "=", endpoint_etranger.id)]
            ), "ni les systèmes eux-mêmes")
        self.assertFalse(
            vus, "les relevés d'un autre client ne doivent pas être visibles"
        )
        paquets = self.env["bf.patch.package"].with_user(curieux).search(
            [("endpoint_id", "=", endpoint_etranger.id)]
        )
        self.assertFalse(paquets, "ni les paquets, qui portent l'inventaire")

    def test_un_releve_ne_peut_pas_repointer_la_machine(self):
        """Un agent compromis ne doit pas pouvoir changer l'identité de sa
        propre fiche : `machine_id` n'est pas un champ du relevé."""
        from ..controllers.agent_api import REPORT_FIELDS
        self.assertNotIn("machine_id", REPORT_FIELDS)
        self.assertNotIn("partner_id", REPORT_FIELDS)
        self.assertNotIn("patch_managed", REPORT_FIELDS)

    def test_deux_releves_trop_rapproches_sont_refuses(self):
        self.system._apply_report({"pending_count": 1, "pending_known": True})
        self.assertTrue(self.system._report_too_soon())


@tagged("post_install", "-at_install")
class TestAuditApi(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({
            "name": "Client d'audit API", "is_company": True,
        })
        cls.endpoint = cls.env["hosting.endpoint"].create({
            "name": "audit-api-01", "partner_id": cls.partner.id,
        })
        cls.endpoint.action_generate_enrol_code()
        cls.code = cls.endpoint.sudo().agent_enrol_code

    def _token(self):
        response = self.url_open(
            BASE + "/enrol",
            data=json.dumps({"code": self.code,
                             "machine_id": uuid.uuid4().hex}).encode(),
            headers={"Content-Type": "application/json"},
        )
        return response.json()["token"]

    def test_l_inconnu_gagne_quand_l_agent_ne_dit_rien(self):
        """🔴 `_cast` rend None pour un booléen faux : sans rattrapage,
        `pending_known: false` se perdait et le champ retombait sur son défaut,
        « fiable ». Un agent muet sur ce point doit être traité comme inconnu,
        jamais comme fiable."""
        token = self._token()
        response = self.url_open(
            BASE + "/report", data=json.dumps({"pending_count": 0}).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {token}"},
        )
        self.assertEqual(response.status_code, 200)
        self.endpoint.invalidate_recordset()
        system = self.env["bf.patch.system"].search(
            [("endpoint_id", "=", self.endpoint.id)], order="id desc", limit=1
        )
        self.assertFalse(system.pending_known)
        self.assertEqual(system.patch_state, "blind")

    def test_un_agent_qui_martele_est_refuse(self):
        token = self._token()
        body = json.dumps({"pending_count": 1, "pending_known": True}).encode()
        headers = {"Content-Type": "application/json",
                   "Authorization": f"Bearer {token}"}
        first = self.url_open(BASE + "/report", data=body, headers=headers)
        self.assertEqual(first.status_code, 200)
        second = self.url_open(BASE + "/report", data=body, headers=headers)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(
            self.env["bf.patch.report"].search_count(
                [("endpoint_id", "=", self.endpoint.id)]
            ), 1,
        )

    def test_un_machine_id_hostile_ne_devient_pas_du_html(self):
        hostile = "<img src=x onerror=alert(1)>"
        self.url_open(
            BASE + "/enrol",
            data=json.dumps({"code": self.code, "machine_id": hostile}).encode(),
            headers={"Content-Type": "application/json"},
        )
        messages = self.env["mail.message"].search([
            ("model", "=", "hosting.endpoint"),
            ("res_id", "=", self.endpoint.id),
        ])
        vise = [m for m in messages if "machine-id" in (m.body or "")]
        self.assertTrue(vise, "le message d'enrôlement devrait exister")
        for message in vise:
            body = str(message.body)
            # Ce qui compte n'est pas l'absence du mot « onerror » : c'est que
            # le balisage soit neutralisé. Le texte hostile doit apparaître
            # comme du TEXTE, jamais comme une balise.
            self.assertNotIn("<img", body)
            self.assertIn("&lt;img", body)
