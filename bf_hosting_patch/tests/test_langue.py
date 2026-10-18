"""La langue des notes que le module écrit sans personne derrière.

L'agent (requête publique) et les travaux planifiés (OdooBot) n'ont pas de lecteur : leurs notes
suivent l'équipe d'hébergement, donc la langue de la société, l'anglais à défaut. La note d'une
maintenance suit la personne assignée. Chaque essai fait tourner le code dans la langue CONTRAIRE
de celle attendue : un texte qui suivrait encore le contexte d'appel tomberait.
"""
import uuid

from odoo import fields
from odoo.tests import TransactionCase, tagged

from ..models.bf_patch_system import STALE_AFTER_HOURS


@tagged("post_install", "-at_install")
class TestLangue(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for code in ("en_US", "fr_CA"):
            cls.env["res.lang"]._activate_lang(code)
        # Activer la langue ne charge pas les termes : sur une base neuve, le libellé de la portée
        # vient de fr_CA.po (sans effet là où il est déjà chargé).
        cls.env["ir.module.module"]._load_module_terms(["bf_hosting_patch"], ["fr_CA"])
        cls.partner = cls.env["res.partner"].create({
            "name": "Client de langue", "is_company": True,
        })
        cls.endpoint = cls.env["hosting.endpoint"].create({
            "name": "langue-01", "partner_id": cls.partner.id,
            "endpoint_type": "workstation",
        })
        cls.system = cls.env["bf.patch.system"].create({
            "name": "langue-01-linux", "endpoint_id": cls.endpoint.id,
            "os_family": "linux", "machine_id": uuid.uuid4().hex,
            "patch_managed": True,
        })

    def _societe(self, lang):
        self.env.company.partner_id.lang = lang

    def _derniere_note(self, rec):
        return self.env["mail.message"].search(
            [("model", "=", rec._name), ("res_id", "=", rec.id)],
            order="id desc", limit=1).body or ""

    def test_la_langue_des_lecteurs_est_celle_de_la_societe(self):
        System = self.env["bf.patch.system"]
        self._societe("fr_CA")
        self.assertEqual(System._bf_reader_lang(), "fr_CA")
        self._societe(False)
        self.assertEqual(System._bf_reader_lang(), "en_US", "à défaut : la langue de la source")

    def test_systeme_muet_annonce_dans_la_langue_de_la_societe(self):
        self._societe("en_US")
        self.system._apply_report({"packages": [], "pending_known": True, "pending_count": 0})
        vieux = fields.Datetime.subtract(fields.Datetime.now(), hours=STALE_AFTER_HOURS + 1)
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE bf_patch_system SET agent_last_report = %s, patch_state = 'ok' WHERE id = %s",
            (vieux, self.system.id))
        self.system.invalidate_recordset()
        self.env["bf.patch.system"].with_context(lang="fr_CA")._cron_refresh_patch_state()
        self.assertEqual(self.system.patch_state, "stale")
        self.assertIn("No report for more than", self._derniere_note(self.system))

    def test_ordre_perime_dans_la_langue_de_la_societe(self):
        self._societe("fr_CA")
        job = self.env["bf.patch.job"].create({"system_id": self.system.id, "scope": "all"})
        vieux = fields.Datetime.subtract(fields.Datetime.now(), days=30)
        self.env.flush_all()
        self.env.cr.execute("UPDATE bf_patch_job SET create_date = %s WHERE id = %s", (vieux, job.id))
        job.invalidate_recordset()
        self.env["bf.patch.job"].with_context(lang="en_US")._cron_expire_jobs()
        self.assertEqual(job.state, "expired")
        self.assertIn("aucun agent ne l'a exécuté", job.output)

    def test_enrolement_note_dans_la_langue_de_la_societe(self):
        """L'agent appelle par une requête publique : sa langue n'est celle de personne."""
        self._societe("fr_CA")
        self.endpoint.action_generate_enrol_code()
        code = self.endpoint.sudo().agent_enrol_code
        self.env["hosting.endpoint"].with_context(lang="en_US")._enrol_agent(
            code, uuid.uuid4().hex, hostname="langue-01-win", os_family="windows")
        self.assertIn("enrôlé", self._derniere_note(self.endpoint))

    def test_note_de_maintenance_dans_la_langue_de_la_personne_assignee(self):
        for lang, attendus in (("fr_CA", ("Poste :", "Instructions :")),
                               ("en_US", ("Endpoint:", "Instructions:"))):
            personne = self.env["res.users"].create({
                "name": f"Technicien {lang}", "login": f"technicien-langue-{lang}",
                "lang": lang,
                "groups_id": [(4, self.env.ref("base.group_user").id)],
            })
            contraire = "en_US" if lang == "fr_CA" else "fr_CA"
            sched = self.env["hosting.maintenance.schedule"].with_context(lang=contraire).create({
                "name": f"Mises à jour {lang}", "frequency": "weekly",
                "maintenance_type": "security_patch", "last_performed": "2026-08-24",
                "endpoint_id": self.endpoint.id, "user_id": personne.id,
                "instructions": "Redémarrer après.",
            })
            self.env.flush_all()
            sched.with_context(lang=contraire)._create_maintenance_activity()
            note = sched.activity_ids[:1].note or ""
            for attendu in attendus:
                self.assertIn(attendu, note, f"{lang} : {note}")

    def test_nom_de_l_ordre_dans_la_langue_de_qui_le_cree(self):
        """Le nom est stocké : il prenait le libellé de la source pour tout le monde."""
        # Lu aussitôt après chaque création, comme dans la requête de qui le crée : un calcul
        # stocké en attente se fait dans l'environnement qui le lit le premier.
        Job = self.env["bf.patch.job"]
        fr = Job.with_context(lang="fr_CA").create({"system_id": self.system.id, "scope": "security"})
        self.assertTrue(fr.name.endswith("Sécurité seulement"), fr.name)
        en = Job.with_context(lang="en_US").create({"system_id": self.system.id, "scope": "security"})
        self.assertTrue(en.name.endswith("Security only"), en.name)
