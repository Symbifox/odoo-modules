"""Les appels d'un contact se retrouvent depuis sa fiche (18.0.5.27.4).

Un appel passé depuis la fiche d'un contact entrait dans Odoo sans contact. La fiche portait « 418 555-0101 », et
l'appariement cherchait « 4185550101 » par ``ilike`` : jamais trouvé. Et même
rattaché, rien sur la fiche ne montrait l'appel.

Ce que ces essais tiennent :

1. un numéro formaté sur la fiche (Mobile ou Téléphone) rattache le fil ;
2. plusieurs fiches pour un même numéro ne désignent personne, sauf le standard
   d'une entreprise inscrit chez elle et chez ses employés : le fil va à elle ;
3. le bouton « Appels » compte les appels du contact et de ses employés, y
   compris ceux d'un fil né archivé, et rien pour qui n'a pas la Messagerie ;
4. la montée rattache les fils restés sans contact, sans toucher aux autres ;
5. relecture adverse du 2026-10-28 : une fiche archivée ne compte pas (même dans
   la montée, qui tourne en active_test=False) ; l'unicité se juge sur toutes les
   fiches, pas sur celles que l'appelant voit ; la montée garde le nom connu du
   fil ; le compteur ne passe pas d'un usager à l'autre ; l'import suit la règle.
"""

import importlib.util

from odoo.modules.module import get_module_path
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("bf_sms_archive", "post_install", "-at_install")
class TestAppelsFicheContact(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(
            cls.env, login="sms_fiche",
            groups="base.group_user,bf_sms_archive.group_sms_user",
        )
        cls.Partner = cls.env["res.partner"]
        cls.Thread = cls.env["sms.archive.thread"]
        cls.Call = cls.env["call.archive.call"]

    def _appel(self, phone, date_ms="1761487800000"):
        return self.Call.with_user(self.user)._ingest_one(
            phone_raw=phone, owner_id=self.user.id, call_type="outgoing",
            date_ms=date_ms, duration=100, contact_name=None, batch_id="essai",
        )

    def _monter(self):
        chemin = get_module_path("bf_sms_archive") + "/migrations/18.0.5.27.4/post-migrate.py"
        spec = importlib.util.spec_from_file_location("migration_27_4", chemin)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.migrate(self.env.cr, "18.0.5.27.3")
        self.env.invalidate_all()

    def _fil(self, phone):
        return self.Thread.with_context(active_test=False).search([
            ("phone_normalized", "like", phone[-4:]), ("owner_id", "=", self.user.id),
        ])

    # ── 1. Numéro formaté ──────────────────────────────────────────

    def test_telephone_formate_rattache(self):
        p = self.Partner.create({"name": "Fiche formatée", "phone": "418 555-0101"})
        self._appel("+14185550101")
        self.assertEqual(self._fil("+14185550101").partner_id, p)

    def test_mobile_formate_rattache(self):
        p = self.Partner.create({"name": "Mobile formaté", "mobile": "+1 (418) 555-0102"})
        self._appel("4185550102")
        self.assertEqual(self._fil("4185550102").partner_id, p)

    # ── 2. Plusieurs fiches ────────────────────────────────────────

    def test_deux_inconnus_meme_numero_personne(self):
        self.Partner.create({"name": "A", "phone": "418 555-0103"})
        self.Partner.create({"name": "B", "phone": "418-555-0103"})
        self._appel("+14185550103")
        self.assertFalse(self._fil("+14185550103").partner_id,
                         "deux fiches sans lien : le fil ne doit aller à aucune")

    def test_standard_entreprise_va_a_l_entreprise(self):
        co = self.Partner.create({"name": "Entreprise standard inc.", "is_company": True, "phone": "418 555-0104"})
        self.Partner.create({"name": "Employé 1", "parent_id": co.id, "phone": "418 555-0104"})
        self.Partner.create({"name": "Employé 2", "parent_id": co.id, "phone": "418 555-0104"})
        self._appel("+14185550104")
        self.assertEqual(self._fil("+14185550104").partner_id, co)

    def test_rattachement_manuel_suit_la_meme_regle(self):
        p = self.Partner.create({"name": "Manuel", "phone": "(418) 555-0105"})
        self._appel("+14185550105")
        fil = self._fil("+14185550105")
        fil.partner_id = False
        fil.with_user(self.user).action_match_partner()
        self.assertEqual(fil.partner_id, p)

    # ── 3. Bouton « Appels » ───────────────────────────────────────

    def test_bouton_compte_contact_et_employes(self):
        co = self.Partner.create({"name": "Entreprise", "is_company": True})
        emp = self.Partner.create({"name": "Employée", "parent_id": co.id, "mobile": "418 555-0106"})
        self._appel("+14185550106", date_ms="1761487800000")
        self._appel("+14185550106", date_ms="1761487900000")
        fil = self._fil("+14185550106")
        self.assertFalse(fil.with_context(active_test=False).active, "le fil d'un appel naît archivé")
        self.assertEqual(emp.with_user(self.user).call_archive_count, 2)
        self.assertEqual(co.with_user(self.user).call_archive_count, 2)
        action = emp.with_user(self.user).action_view_call_archive()
        self.assertEqual(
            self.Call.with_user(self.user).with_context(action["context"]).search_count(action["domain"]), 2)

    def test_bouton_vide_sans_la_messagerie(self):
        p = self.Partner.create({"name": "Vu par un autre", "phone": "418 555-0107"})
        self._appel("+14185550107")
        autre = new_test_user(self.env, login="sans_sms", groups="base.group_user")
        self.assertEqual(p.with_user(autre).call_archive_count, 0)

    # ── 4. Montée ──────────────────────────────────────────────────

    def test_montee_rattache_les_fils_orphelins(self):
        orphelin = self.Thread.create({"phone_normalized": "+14185550108", "owner_id": self.user.id})
        deja = self.Thread.create({"phone_normalized": "+14185550109", "owner_id": self.user.id})
        autre = self.Partner.create({"name": "Déjà choisi"})
        deja.partner_id = autre
        p = self.Partner.create({"name": "Orpheline", "phone": "418 555-0108"})
        self.Partner.create({"name": "Concurrent", "phone": "418 555-0109"})
        self._monter()
        self.assertEqual(orphelin.partner_id, p)
        self.assertEqual(deja.partner_id, autre, "un fil déjà rattaché n'est jamais touché")

    # ── 5. Relecture adverse ───────────────────────────────────────

    def test_montee_ignore_une_fiche_archivee(self):
        orphelin = self.Thread.create({"phone_normalized": "+14185550110", "owner_id": self.user.id})
        self.Partner.create({"name": "Ancien client", "phone": "418 555-0110", "active": False})
        self._monter()
        self.assertFalse(orphelin.partner_id, "un numéro sur une fiche archivée ne désigne personne")

    def test_fiche_archivee_ne_rend_pas_ambigu(self):
        p = self.Partner.create({"name": "Nouveau titulaire", "phone": "418 555-0111"})
        self.Partner.create({"name": "Ancien titulaire", "phone": "418 555-0111", "active": False})
        self._appel("+14185550111")
        self.assertEqual(self._fil("+14185550111").partner_id, p)

    def test_montee_garde_le_nom_du_fil(self):
        orphelin = self.Thread.create({"phone_normalized": "+14185550112", "owner_id": self.user.id,
                                       "contact_name": "Nom venu du téléphone"})
        co = self.Partner.create({"name": "Standard inc.", "is_company": True, "phone": "418 555-0112"})
        self.Partner.create({"name": "Employé", "parent_id": co.id, "phone": "418 555-0112"})
        self._monter()
        self.assertEqual(orphelin.partner_id, co)
        self.assertEqual(orphelin.contact_name, "Nom venu du téléphone")

    def test_unicite_jugee_sur_toutes_les_fiches(self):
        autre_societe = self.env["res.company"].create({"name": "Autre société A"})
        self.Partner.create({"name": "Visible", "phone": "418 555-0113"})
        self.Partner.create({"name": "Cachée", "phone": "418 555-0113", "company_id": autre_societe.id})
        self.assertNotIn(autre_societe, self.user.company_ids)
        self._appel("+14185550113")
        fil = self._fil("+14185550113")
        fil.partner_id = False
        fil.with_user(self.user).action_match_partner()
        self.assertFalse(fil.partner_id, "deux fiches : ambigu même si l'usager n'en voit qu'une")

    def test_fiche_illisible_pas_rendue(self):
        autre_societe = self.env["res.company"].create({"name": "Autre société B"})
        self.Partner.create({"name": "Seule mais cachée", "phone": "418 555-0114",
                             "company_id": autre_societe.id})
        self.assertFalse(self.Thread.with_user(self.user)._match_partner("+14185550114"))

    def test_compteur_par_usager(self):
        p = self.Partner.create({"name": "Deux regards", "phone": "418 555-0115"})
        self._appel("+14185550115")
        autre = new_test_user(self.env, login="sms_autre",
                              groups="base.group_user,bf_sms_archive.group_sms_user")
        self.assertEqual(p.with_user(self.user).call_archive_count, 1)
        self.assertEqual(p.with_user(autre).call_archive_count, 0,
                         "le compte d'un usager ne doit pas servir à un autre")

    def test_import_suit_la_meme_regle(self):
        # Sans formatage : l'ancien ilike + limit=1 les trouvait et prenait la première.
        self.Partner.create({"name": "A", "phone": "4185550116"})
        self.Partner.create({"name": "B", "phone": "4185550116"})
        p = self.Partner.create({"name": "Seule", "mobile": "418 555-0117"})
        ambigu = self.Thread.create({"phone_normalized": "+14185550116", "owner_id": self.user.id})
        seul = self.Thread.create({"phone_normalized": "+14185550117", "owner_id": self.user.id})
        self.env["sms.archive.import.wizard"]._match_partners({}, self.user.id)
        self.assertFalse(ambigu.partner_id)
        self.assertEqual(seul.partner_id, p)

    def test_ingestion_et_montee_jugent_pour_le_proprietaire(self):
        soc = self.env["res.company"].create({"name": "Autre société C"})
        self.Partner.create({"name": "Autre société", "phone": "418 555-0118", "company_id": soc.id})
        self._appel("+14185550118")
        self.assertFalse(self._fil("+14185550118").partner_id,
                         "une fiche que le propriétaire ne lit pas ferait tomber sa Messagerie")
        orphelin = self.Thread.create({"phone_normalized": "+14185550119", "owner_id": self.user.id})
        self.Partner.create({"name": "Autre société 2", "phone": "418 555-0119", "company_id": soc.id})
        self._monter()
        self.assertFalse(orphelin.partner_id)
