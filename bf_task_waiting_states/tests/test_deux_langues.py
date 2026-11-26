"""Les deux états d'attente se lisent en anglais en en_US/en_CA, en français en fr_CA.

L'essai mesure le catalogue LIVRÉ, pas le français déjà en base : il retire la clé
fr_CA des deux libellés, recharge le catalogue du module en écrasant, puis lit.
Il tient sur une base neuve en en_US seul (CI publique) : rien n'y dépend du
catalogue de `project`.
"""
from odoo.tests import TransactionCase, tagged

ATTENDU = {
    "05_waiting_client": ("Waiting on Client", "Attente - Client"),
    "06_waiting_external": ("Waiting on External", "Attente - Externe"),
}
SOCLE = ("01_in_progress", "1_done")

LIBELLES_SQL = """
    SELECT s.value, s.name FROM ir_model_fields_selection s
      JOIN ir_model_fields f ON f.id = s.field_id
     WHERE f.model = 'project.task' AND f.name = 'state' AND s.value IN %s
"""


@tagged("post_install", "-at_install")
class TestDeuxLangues(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Lang = cls.env["res.lang"]
        for code in ("fr_CA", "en_CA"):
            Lang._activate_lang(code)
        cr = cls.env.cr
        cr.execute(LIBELLES_SQL, [SOCLE])
        cls.socle_avant = dict(cr.fetchall())
        cr.execute(
            """
            UPDATE ir_model_fields_selection s SET name = s.name - 'fr_CA'
              FROM ir_model_fields f
             WHERE f.id = s.field_id AND f.model = 'project.task' AND f.name = 'state'
               AND s.value IN %s
            """,
            [tuple(ATTENDU)],
        )
        # Sans les deux lignes, le français déjà en base resterait et l'essai
        # passerait sans rien mesurer du catalogue.
        assert cr.rowcount == len(ATTENDU), cr.rowcount
        cls.env.invalidate_all()
        cls.env["ir.module.module"]._load_module_terms(
            ["bf_task_waiting_states"], ["fr_CA"], overwrite=True)
        cls.env.invalidate_all()
        cls.env.registry.clear_cache()

    def _libelles(self, lang):
        env = self.env(context=dict(self.env.context, lang=lang))
        return dict(env["project.task"]._fields["state"]._description_selection(env))

    def test_anglais(self):
        for lang in ("en_US", "en_CA"):
            libelles = self._libelles(lang)
            for valeur, (anglais, _francais) in ATTENDU.items():
                self.assertEqual(libelles.get(valeur), anglais, (lang, valeur))

    def test_francais(self):
        libelles = self._libelles("fr_CA")
        for valeur, (_anglais, francais) in ATTENDU.items():
            self.assertEqual(libelles.get(valeur), francais, valeur)

    def test_etats_du_socle_intacts(self):
        # Le catalogue du module ne touche que ses deux états : les libellés des
        # états de `project`, toutes langues, sont ceux d'avant le rechargement.
        self.env.cr.execute(LIBELLES_SQL, [SOCLE])
        self.assertEqual(dict(self.env.cr.fetchall()), self.socle_avant)
        self.assertEqual(set(self.socle_avant), set(SOCLE))
