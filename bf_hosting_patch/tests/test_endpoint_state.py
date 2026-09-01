# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""L'état d'un système, l'agrégat de la machine, et le double amorçage."""

import uuid

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from ..models.bf_patch_system import STALE_AFTER_HOURS


@tagged("post_install", "-at_install")
class TestPatchState(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({
            "name": "Client de banc", "is_company": True,
        })
        cls.endpoint = cls.env["hosting.endpoint"].create({
            "name": "banc-01", "partner_id": cls.partner.id,
            "endpoint_type": "workstation",
        })
        cls.linux = cls._system(cls, "banc-01-linux", "linux")

    def _system(self, name, family="linux", endpoint=None):
        return self.env["bf.patch.system"].create({
            "name": name, "endpoint_id": (endpoint or self.endpoint).id,
            "os_family": family, "machine_id": uuid.uuid4().hex,
            "patch_managed": True,
        })

    def _report(self, system=None, **values):
        payload = dict(values)
        payload.setdefault("packages", [])
        payload.setdefault("pending_known", True)
        return (system or self.linux)._apply_report(payload)

    def _laisser_passer_le_temps(self, system):
        """Poser l'état que le monde produit tout seul : un dernier relevé
        vieux, et un `patch_state` encore vert parce que RIEN n'a été écrit
        depuis. Passer par l'ORM rejouerait le calcul et masquerait le cas."""
        stale = fields.Datetime.subtract(
            fields.Datetime.now(), hours=STALE_AFTER_HOURS + 1
        )
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE bf_patch_system SET agent_last_report = %s, "
            "patch_state = 'ok' WHERE id = %s", (stale, system.id),
        )
        system.invalidate_recordset()

    # -- état d'un système -------------------------------------------
    def test_a_jour_apres_un_releve_vide(self):
        self._report(pending_count=0)
        self.assertEqual(self.linux.patch_state, "ok")

    def test_la_securite_prime_sur_le_redemarrage(self):
        self._report(pending_count=9, pending_security_count=2,
                     reboot_required=True)
        self.assertEqual(self.linux.patch_state, "security")

    def test_un_compte_inconnu_ne_passe_jamais_au_vert(self):
        self._report(pending_known=False, pending_count=0)
        self.assertEqual(self.linux.patch_state, "blind")

    def test_un_systeme_muet_bascule_et_le_dit(self):
        self._report(pending_count=0)
        self.assertEqual(self.linux.patch_state, "ok")
        self._laisser_passer_le_temps(self.linux)
        before = self.env["mail.message"].search_count(
            [("model", "=", "bf.patch.system"), ("res_id", "=", self.linux.id)]
        )
        self.env["bf.patch.system"]._cron_refresh_patch_state()
        self.assertEqual(self.linux.patch_state, "stale")
        after = self.env["mail.message"].search_count(
            [("model", "=", "bf.patch.system"), ("res_id", "=", self.linux.id)]
        )
        self.assertGreater(after, before, "le silence doit laisser une trace")

    # -- double amorçage ---------------------------------------------
    def test_une_machine_porte_deux_systemes(self):
        """Le parc est presque tout en double amorçage : deux systèmes sur une
        seule fiche de parc, et donc une seule série, une seule garantie."""
        windows = self._system("banc-01-win", "windows")
        self.assertEqual(len(self.endpoint.system_ids), 2)
        self.assertEqual(self.endpoint.system_count, 2)
        self.assertNotEqual(self.linux.machine_id, windows.machine_id)

    def test_la_machine_prend_le_PIRE_de_ses_systemes(self):
        """Une machine dont le côté Linux est à jour et le côté Windows muet
        n'est pas « à jour » : elle est muette."""
        windows = self._system("banc-01-win", "windows")
        self._report(system=self.linux, pending_count=0)
        self._report(system=windows, pending_count=0)
        self.assertEqual(self.endpoint.patch_state, "ok")

        self._laisser_passer_le_temps(windows)
        self.env["bf.patch.system"]._cron_refresh_patch_state()
        self.endpoint.invalidate_recordset()
        self.assertEqual(windows.patch_state, "stale")
        self.assertEqual(self.linux.patch_state, "ok")
        self.assertEqual(self.endpoint.patch_state, "stale",
                         "l'agrégat doit remonter le pire, pas le meilleur")

    def test_une_machine_sans_systeme_suivi_est_non_suivie(self):
        vierge = self.env["hosting.endpoint"].create({
            "name": "banc-vierge", "partner_id": self.partner.id,
        })
        self.assertEqual(vierge.patch_state, "unmanaged")
        self.assertFalse(vierge.patch_managed)

    def test_le_dernier_releve_de_la_machine_est_le_plus_recent(self):
        windows = self._system("banc-01-win", "windows")
        self._report(system=self.linux, pending_count=1)
        self._report(system=windows, pending_count=1)
        self.endpoint.invalidate_recordset()
        self.assertEqual(
            self.endpoint.agent_last_report,
            max(self.linux.agent_last_report, windows.agent_last_report),
        )

    # -- quick wins ---------------------------------------------------
    def test_le_machine_id_interdit_le_doublon_de_SYSTEME(self):
        partage = uuid.uuid4().hex
        self.linux.machine_id = partage
        jumeau = self._system("banc-01-bis")
        with self.assertRaises(Exception):
            jumeau.machine_id = partage
            jumeau.flush_recordset()

    def test_l_uuid_materiel_interdit_le_doublon_de_MACHINE(self):
        materiel = uuid.uuid4().hex
        self.endpoint.machine_uuid = materiel
        autre = self.env["hosting.endpoint"].create({
            "name": "banc-02", "partner_id": self.partner.id,
        })
        with self.assertRaises(Exception):
            autre.machine_uuid = materiel
            autre.flush_recordset()

    def test_les_jours_d_attente_du_redemarrage(self):
        self._report(reboot_required=True,
                     reboot_pending_since=fields.Datetime.subtract(
                         fields.Datetime.now(), days=9))
        self.assertEqual(self.linux.reboot_pending_days, 9)

    def test_le_disque_serre_se_signale(self):
        self._report(disk_root_pct=92, disk_boot_pct=76)
        self.assertTrue(self.linux.disk_tight)
        self._report(disk_root_pct=64, disk_boot_pct=64)
        self.assertFalse(self.linux.disk_tight)

    def test_l_ecart_se_calcule_sur_le_releve_precedent_du_MEME_systeme(self):
        windows = self._system("banc-01-win", "windows")
        self._report(system=self.linux, pending_count=150)
        self._report(system=windows, pending_count=3)
        self._report(system=self.linux, pending_count=153)
        self.assertEqual(self.linux.pending_delta, 3,
                         "l'écart ne doit pas traverser les systèmes")

    def test_le_rayon_de_souffle_vient_du_serveur(self):
        server = self.env["hosting.server"].create({
            "name": "banc-serveur", "code": "BNC", "hostname": "banc-serveur",
        })
        self.endpoint.server_id = server
        self.assertEqual(self.endpoint.hosted_service_count,
                         server.service_count)

    # -- relevés ------------------------------------------------------
    def test_le_releve_recopie_ses_champs_sur_le_systeme(self):
        report = self._report(package_manager="dnf", pending_count=1,
                              kernel_running="7.1.8", kernel_installed="7.1.10",
                              auto_update_mode="download",
                              os_support_state="ending_soon")
        self.assertEqual(self.linux.agent_last_report, report.report_date)
        self.assertEqual(self.linux.kernel_installed, "7.1.10")
        self.assertEqual(self.linux.auto_update_mode, "download")

    def test_le_releve_remonte_jusqu_a_la_machine(self):
        report = self._report(pending_count=2)
        self.assertEqual(report.endpoint_id, self.endpoint)
        self.assertEqual(report.partner_id, self.partner)

    def test_la_purge_epargne_les_releves_recents(self):
        self._report(pending_count=1)
        vieux = self.env["bf.patch.report"].create({
            "system_id": self.linux.id,
            "report_date": fields.Datetime.subtract(
                fields.Datetime.now(), days=120),
        })
        self.env["bf.patch.report"]._cron_purge()
        self.assertFalse(vieux.exists())
        self.assertTrue(self.linux.report_ids)

    # -- enrôlement ---------------------------------------------------
    def test_un_code_expire_ne_vaut_rien(self):
        self.endpoint.sudo().write({
            "agent_enrol_code": "perime",
            "agent_enrol_expiry": fields.Datetime.subtract(
                fields.Datetime.now(), hours=1),
        })
        with self.assertRaises(UserError):
            self.env["hosting.endpoint"]._enrol_agent("perime", uuid.uuid4().hex)

    def test_l_enrolement_refuse_un_systeme_deja_sur_une_autre_machine(self):
        deja_pris = self.linux.machine_id
        autre = self.env["hosting.endpoint"].create({
            "name": "banc-03", "partner_id": self.partner.id,
        })
        autre.action_generate_enrol_code()
        with self.assertRaises(UserError):
            self.env["hosting.endpoint"]._enrol_agent(
                autre.sudo().agent_enrol_code, deja_pris)

    def test_un_reenrolement_fait_TOURNER_le_jeton_sans_empiler(self):
        """La même installation qui rejoue son enrôlement ne doit pas produire
        une deuxième fiche de système."""
        mid = uuid.uuid4().hex
        self.endpoint.action_generate_enrol_code()
        system, premier = self.env["hosting.endpoint"]._enrol_agent(
            self.endpoint.sudo().agent_enrol_code, mid, hostname="banc-01")
        avant = len(self.endpoint.system_ids)
        self.endpoint.action_generate_enrol_code()
        rejoue, second = self.env["hosting.endpoint"]._enrol_agent(
            self.endpoint.sudo().agent_enrol_code, mid, hostname="banc-01")
        self.assertEqual(rejoue, system)
        self.assertEqual(len(self.endpoint.system_ids), avant)
        self.assertNotEqual(premier, second)
        self.assertFalse(
            self.env["bf.patch.system"]._resolve_agent(premier),
            "l'ancien jeton doit cesser de valoir",
        )

    def test_l_uuid_materiel_ne_s_ecrase_pas_en_silence(self):
        self.endpoint.machine_uuid = "premier-uuid"
        self.endpoint.action_generate_enrol_code()
        self.env["hosting.endpoint"]._enrol_agent(
            self.endpoint.sudo().agent_enrol_code, uuid.uuid4().hex,
            machine_uuid="autre-uuid")
        self.endpoint.invalidate_recordset()
        self.assertEqual(self.endpoint.machine_uuid, "premier-uuid")

    def test_la_revocation_ferme_la_porte(self):
        self.endpoint.action_generate_enrol_code()
        system, token = self.env["hosting.endpoint"]._enrol_agent(
            self.endpoint.sudo().agent_enrol_code, uuid.uuid4().hex)
        system.action_revoke_agent()
        self.assertFalse(self.env["bf.patch.system"]._resolve_agent(token))
