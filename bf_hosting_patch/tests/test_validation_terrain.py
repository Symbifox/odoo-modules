# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Les trois trous trouvés en éprouvant le module pour de vrai.

Chacun a été reproduit d'abord sur un banc peuplé d'une copie de la production
et d'une vraie machine jetable qui parlait à l'API, puis refermé ici.
"""

import uuid

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from ..models.bf_patch_job import LOCAL_CONSENT_REFUSED


@tagged("post_install", "-at_install")
class TestValidation25626(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({
            "name": "Client de banc 25626", "is_company": True,
        })
        cls.endpoint = cls.env["hosting.endpoint"].create({
            "name": "banc-25626-a", "partner_id": cls.partner.id,
            "endpoint_type": "workstation",
        })
        cls.autre = cls.env["hosting.endpoint"].create({
            "name": "banc-25626-b", "partner_id": cls.partner.id,
            "endpoint_type": "workstation",
        })

    def _code(self, endpoint):
        endpoint.action_generate_enrol_code()
        return endpoint.sudo().agent_enrol_code

    # -- 1. l'UUID matériel déjà porté par une autre fiche -------------
    def test_uuid_materiel_deja_au_parc_refus_lisible(self):
        """Le doublon de machine se dit, au lieu de sortir en HTTP 400 muet.

        Avant : l'écriture violait la contrainte d'unicité, la violation
        remontait hors du `try` du contrôleur, et l'agent n'avait qu'un
        « HTTP 400 » à imprimer devant la personne qui enrôle.
        """
        materiel = str(uuid.uuid4())
        self.autre.sudo().write({"machine_uuid": materiel})
        with self.assertRaises(UserError) as refus:
            self.env["hosting.endpoint"]._enrol_agent(
                self._code(self.endpoint), uuid.uuid4().hex,
                hostname="jumelle", machine_uuid=materiel,
            )
        self.assertIn("banc-25626-b", str(refus.exception))

    def test_uuid_materiel_sur_une_fiche_ARCHIVEE_aussi(self):
        """⚠️ L'index d'unicité couvre les fiches archivées ; une recherche
        ordinaire ne les voit pas et laisserait repasser la violation."""
        materiel = str(uuid.uuid4())
        self.autre.sudo().write({"machine_uuid": materiel})
        self.autre.sudo().action_archive()
        with self.assertRaises(UserError):
            self.env["hosting.endpoint"]._enrol_agent(
                self._code(self.endpoint), uuid.uuid4().hex,
                machine_uuid=materiel,
            )

    def test_le_meme_uuid_sur_la_MEME_fiche_ne_gene_pas(self):
        """Ré-enrôler la même machine sur sa propre fiche reste possible."""
        materiel = str(uuid.uuid4())
        self.endpoint.sudo().write({"machine_uuid": materiel})
        system, token = self.env["hosting.endpoint"]._enrol_agent(
            self._code(self.endpoint), uuid.uuid4().hex,
            machine_uuid=materiel,
        )
        self.assertTrue(token)
        self.assertEqual(system.endpoint_id, self.endpoint)

    # -- 2. les compteurs d'avant l'application ------------------------
    def _system(self):
        return self.env["bf.patch.system"].create({
            "name": "banc-25626-sys", "endpoint_id": self.endpoint.id,
            "os_family": "linux", "machine_id": uuid.uuid4().hex,
            "patch_managed": True,
        })

    def test_un_ordre_applique_apres_le_releve_le_dit(self):
        system = self._system()
        system._apply_report({"pending_count": 50, "pending_known": True,
                              "packages": []})
        # ⚠️ Le relevé et la fin de l'ordre tombent dans la MÊME seconde en
        # test, et l'égalité est volontairement tranchée en faveur du silence :
        # un avis qui ne s'efface jamais serait pire que l'avis manqué d'une
        # seconde. On recule donc le relevé pour que la chronologie soit celle
        # du monde réel — on relève, puis on applique.
        system.sudo().write({"agent_last_report": fields.Datetime.subtract(
            fields.Datetime.now(), seconds=30)})
        self.assertFalse(system.applied_after_report)
        job = self.env["bf.patch.job"].create({
            "system_id": system.id, "scope": "all", "reboot_after": "never",
        })
        job._record_result("done", exit_code=0, output="ok",
                           packages_changed=12)
        system.invalidate_recordset()
        self.assertTrue(
            system.applied_after_report,
            "après une application réussie, la fiche doit dire que ses "
            "compteurs sont antérieurs",
        )
        # Le relevé suivant referme l'avis.
        system._apply_report({"pending_count": 38, "pending_known": True,
                              "packages": []})
        system.invalidate_recordset()
        self.assertFalse(system.applied_after_report)

    def test_un_ordre_echoue_ne_perime_aucun_compteur(self):
        system = self._system()
        system._apply_report({"pending_count": 50, "pending_known": True,
                              "packages": []})
        job = self.env["bf.patch.job"].create({
            "system_id": system.id, "scope": "all", "reboot_after": "never",
        })
        job._record_result("failed", exit_code=100, output="apt a échoué")
        system.invalidate_recordset()
        self.assertFalse(system.applied_after_report)

    # -- 3. le refus local vaut révocation -----------------------------
    def test_refus_local_retire_le_consentement_de_la_fiche(self):
        """Sans ça, la fiche gardait « application autorisée » jusqu'au relevé
        du lendemain, et le serveur continuait de remettre des ordres à une
        machine dont le propriétaire venait de les refuser."""
        system = self._system()
        system._apply_report({"pending_count": 1, "pending_known": True,
                              "apply_allowed": True, "packages": []})
        self.assertTrue(system.apply_allowed)
        job = self.env["bf.patch.job"].create({
            "system_id": system.id, "scope": "all", "reboot_after": "never",
        })
        job._record_result("failed", exit_code=LOCAL_CONSENT_REFUSED,
                           output="refus : le fichier n'existe pas")
        system.invalidate_recordset()
        self.assertFalse(system.apply_allowed)
        # Et la file se referme aussi : plus rien ne part.
        self.assertEqual(
            self.env["bf.patch.job"]._claim_for(system)[1],
            "consentement local absent sur la machine",
        )

    def test_un_autre_echec_ne_touche_pas_au_consentement(self):
        system = self._system()
        system._apply_report({"pending_count": 1, "pending_known": True,
                              "apply_allowed": True, "packages": []})
        job = self.env["bf.patch.job"].create({
            "system_id": system.id, "scope": "all", "reboot_after": "never",
        })
        job._record_result("failed", exit_code=100, output="apt a échoué")
        system.invalidate_recordset()
        self.assertTrue(system.apply_allowed)

    def test_le_releve_suivant_rouvre_le_consentement(self):
        system = self._system()
        system._apply_report({"pending_known": True, "apply_allowed": True,
                              "packages": []})
        job = self.env["bf.patch.job"].create({
            "system_id": system.id, "scope": "all", "reboot_after": "never",
        })
        job._record_result("failed", exit_code=LOCAL_CONSENT_REFUSED,
                           output="refus")
        system.invalidate_recordset()
        self.assertFalse(system.apply_allowed)
        system._apply_report({"pending_known": True, "apply_allowed": True,
                              "packages": []})
        self.assertTrue(system.apply_allowed)
