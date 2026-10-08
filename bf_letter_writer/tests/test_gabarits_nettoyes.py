"""Modèles de lettres et blocs de texte nettoyés à l'écriture."""
from odoo.tests import TransactionCase, tagged

PIEGE = '<img src="x" onerror="alert(1)"/><script>alert(2)</script><a href="javascript:alert(3)">j</a>'
CORPS = (
    '<p style="text-align: center; color: #29ABE1;">Bonjour {{ object.partner_id.name }},</p>'
    '<p>QWeb : <t t-out="object.partner_id.name"/></p>'
    '<p><a href="https://exemple.invalid/dossier">dossier</a></p>'
)


@tagged("post_install", "-at_install")
class TestGabaritsNettoyes(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({"name": "QA Gabarit Inc."})

    def _sans_piege(self, html):
        html = str(html)
        for bout in ("onerror", "<script", "alert(2)", "javascript:"):
            self.assertNotIn(bout, html)

    def test_modele_nettoye_champs_de_fusion_gardes(self):
        tpl = self.env["letter.template"].create({"name": "QA", "body_html": CORPS + PIEGE})
        self._sans_piege(tpl.body_html)
        for garde in ("{{ object.partner_id.name }}", 't-out="object.partner_id.name"',
                      "text-align", "#29ABE1", "https://exemple.invalid/dossier"):
            self.assertIn(garde, str(tpl.body_html))
        # Le rendu réel : les deux formes de champ de fusion donnent le nom.
        letter = self.env["letter.document"].create({
            "partner_id": self.partner.id, "template_id": tpl.id})
        letter.action_apply_template()
        body = str(letter.body_html)
        self.assertEqual(body.count("QA Gabarit Inc."), 2)
        self.assertNotIn("{{", body)
        self.assertIn("text-align", body)
        self.assertIn('href="https://exemple.invalid/dossier"', body)

    def test_bloc_de_texte_nettoye(self):
        bloc = self.env["letter.quicktext"].create({"name": "QA bloc", "body_html": CORPS + PIEGE})
        self._sans_piege(bloc.body_html)
        self.assertIn("{{ object.partner_id.name }}", str(bloc.body_html))
        letter = self.env["letter.document"].create({"partner_id": self.partner.id})
        letter.body_html = "<p>Début.</p>"
        self.env["letter.quicktext.picker"].create({
            "letter_id": letter.id, "quicktext_id": bloc.id}).action_insert()
        self.assertIn("QA Gabarit Inc.", str(letter.body_html))
        self._sans_piege(letter.body_html)


def _migration(module, version):
    import importlib.util
    import os
    chemin = os.path.join(os.path.dirname(os.path.dirname(__file__)), "migrations", version,
                          "post-migrate.py")
    spec = importlib.util.spec_from_file_location(f"{module}_mig_nettoyage", chemin)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@tagged("post_install", "-at_install")
class TestMigrationCorpsExistants(TransactionCase):
    """La montée nettoie les corps écrits avant le nettoyage, et journalise."""

    def test_corps_piege_nettoye_corps_propre_intact(self):
        partner = self.env["res.partner"].create({"name": "QA Migration"})
        lettre = self.env["letter.document"].create({"partner_id": partner.id})
        tpl = self.env["letter.template"].create({"name": "QA mig"})
        bloc = self.env["letter.quicktext"].create({"name": "QA mig bloc"})
        propre = self.env["letter.template"].create({"name": "QA propre"})
        corps_propre = '<p style="text-align: center;">Bonjour <t t-out="object.partner_id.name"></t></p>'
        for table, rid, corps in (("letter_document", lettre.id, "<p>Lettre</p>" + PIEGE),
                                  ("letter_template", tpl.id, CORPS + PIEGE),
                                  ("letter_quicktext", bloc.id, CORPS + PIEGE),
                                  ("letter_template", propre.id, corps_propre)):
            self.env.cr.execute(f"UPDATE {table} SET body_html = %s WHERE id = %s", [corps, rid])
        with self.assertLogs("bf_letter_writer_mig_nettoyage", level="WARNING") as journal:
            _migration("bf_letter_writer", "18.0.2.0.2").migrate(self.env.cr, "18.0.2.0.1")
        self.env.invalidate_all()
        for rec in (lettre, tpl, bloc):
            self._sans_piege(rec.body_html)
            self.assertIn(f"modèle={rec._name} id={rec.id}", "\n".join(journal.output))
        self.assertIn('t-out="object.partner_id.name"', str(tpl.body_html))
        self.env.cr.execute("SELECT body_html FROM letter_template WHERE id = %s", [propre.id])
        self.assertEqual(self.env.cr.fetchone()[0], corps_propre)
        self.assertNotIn(f"id={propre.id} ", "\n".join(journal.output).replace("letter.template id=%s" % propre.id, "X"))

    def _sans_piege(self, html):
        html = str(html)
        for bout in ("onerror", "<script", "alert(2)", "javascript:"):
            self.assertNotIn(bout, html)
