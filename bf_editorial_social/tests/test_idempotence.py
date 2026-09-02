# -*- coding: utf-8 -*-
"""La garantie de non-doublon est la raison d'être de ce module.

Un travail périodique qui reprend une file après une coupure réseau
republie, si rien ne l'en empêche. Ces tests existent pour que ce « rien »
n'arrive jamais.
"""

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged
from psycopg2 import IntegrityError
from odoo.tools import mute_logger


class FauxConnecteur:
    """Compte les appels sortants au lieu d'en faire."""
    appels = 0


@tagged("post_install", "-at_install")
class TestIdempotence(TransactionCase):

    def setUp(self):
        super().setUp()
        self.cal = self.env["bf.editorial.calendar"].create({
            "name": "Diffusion", "require_all_langs": "no", "word_floor": 10,
        })
        self.entry = self.env["bf.editorial.entry"].create({
            "name": "Article d'essai", "calendar_id": self.cal.id, "qa_state": "clean",
        })
        self.entry.checklist_ids.unlink()
        lang = self.env["res.lang"].search([("active", "=", True)], limit=1)
        self.canal = self.env["bf.social.channel"].create({
            "name": "Canal d'essai", "network": "bluesky",
            "handle": "essai.test", "lang_id": lang.id, "login": "essai.test",
        })

    def _billet(self, **kw):
        vals = {"entry_id": self.entry.id, "channel_id": self.canal.id,
                "body": "Un texte court.", "state": "scheduled",
                "scheduled_datetime": "2000-01-01 00:00:00"}
        vals.update(kw)
        return self.env["bf.social.post"].create(vals)

    def test_cle_posee_a_la_creation(self):
        p = self._billet()
        self.assertTrue(p.idempotency_key, "un billet naît avec sa clé")

    def test_deux_billets_ne_partagent_pas_leur_cle(self):
        a, b = self._billet(), self._billet()
        self.assertNotEqual(a.idempotency_key, b.idempotency_key)

    @mute_logger("odoo.sql_db")
    def test_cle_dupliquee_refusee_par_la_base(self):
        a = self._billet()
        with self.assertRaises(IntegrityError):
            with self.cr.savepoint():
                self._billet(idempotency_key=a.idempotency_key)

    @mute_logger("odoo.sql_db")
    def test_meme_billet_distant_refuse_deux_fois(self):
        self._billet(remote_id="at://x/1", state="sent")
        with self.assertRaises(IntegrityError):
            with self.cr.savepoint():
                self._billet(remote_id="at://x/1", state="sent")

    def test_un_billet_deja_diffuse_ne_repart_pas(self):
        p = self._billet(remote_id="at://x/2", state="sent")
        self.assertIn("Déjà diffusé", " ".join(p._blocking_reasons()))
        self.assertFalse(p._claim_and_send(), "un billet avec identifiant distant ne repart jamais")

    def test_le_cron_ignore_ce_qui_porte_un_identifiant(self):
        p = self._billet(remote_id="at://x/3", state="scheduled")
        self.env["bf.social.post"]._cron_send_scheduled()
        self.assertEqual(p.state, "scheduled", "le cron ne doit pas le reprendre")

    def test_texte_trop_long_bloque(self):
        p = self._billet(body="x" * 400)
        self.assertTrue(p.over_limit)
        self.assertIn("trop long", " ".join(p._blocking_reasons()).lower())
        with self.assertRaises(UserError):
            p.action_send_now()

    def test_la_garde_de_l_article_vaut_aussi_ici(self):
        """Un texte que le module refuse de publier ne se diffuse pas non plus."""
        self.entry.qa_state = "todo"
        self.entry.invalidate_recordset()
        p = self._billet()
        self.assertIn("pré-vol", " ".join(p._blocking_reasons()))

    def test_identifiants_refuses_bloquent_le_canal(self):
        self.canal.credentials_state = "ko"
        p = self._billet()
        self.assertIn("identifiants", " ".join(p._blocking_reasons()).lower())

    def test_cron_marque_en_echec_sans_appeler_le_reseau(self):
        self.canal.credentials_state = "ko"
        p = self._billet()
        self.env["bf.social.post"]._cron_send_scheduled()
        self.assertEqual(p.state, "failed")
        self.assertFalse(p.remote_id)

    def test_le_fonds_ne_se_bloque_pas_sur_la_dette_de_style(self):
        """Vécu au premier recyclage réel : un billet public depuis un an,
        à 14 600 visites, était refusé parce que sa QA relevait des titres
        vides. La QA garde la publication, pas le fait de pointer vers du
        déjà public."""
        self.entry.qa_state = "findings"
        self.entry.published_date = "2025-01-01 12:00:00"
        lang = self.canal.lang_id
        self.env["bf.editorial.version"].create({
            "entry_id": self.entry.id, "lang_id": lang.id,
            "is_source": True, "state": "published",
        })
        self.entry.invalidate_recordset()
        self.assertFalse(self.entry.preflight_ok, "la QA doit bien être rouge")
        # Le lien est une exigence distincte du style : un billet issu de la
        # mise en file en porte toujours un, on le pose donc ici aussi pour
        # que ce test ne parle que de ce qu'il vise.
        recycle = self._billet(kind="recycle",
                               link_url="https://exemple.test/article")
        self.assertEqual(recycle._blocking_reasons(), [],
                         "un article du fonds ne se bloque pas sur du style")
        nouveau = self._billet(kind="new")
        self.assertIn("pré-vol", " ".join(nouveau._blocking_reasons()),
                      "une nouveauté, elle, reste tenue par la garde")

    def test_le_fonds_se_bloque_sur_une_derive_de_version(self):
        self.entry.published_date = "2025-01-01 12:00:00"
        module = self.env["ir.module.module"].search([("state", "=", "installed")], limit=1)
        self.entry.write({"subject_module_id": module.id, "source_version": "0.0.0.1"})
        lang = self.canal.lang_id
        self.env["bf.editorial.version"].create({
            "entry_id": self.entry.id, "lang_id": lang.id,
            "is_source": True, "state": "published",
        })
        self.entry.invalidate_recordset()
        self.assertTrue(self.entry.version_drift)
        self.assertIn("a changé", " ".join(self._billet(kind="recycle")._blocking_reasons()))
