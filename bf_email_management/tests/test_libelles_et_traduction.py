"""Deux gardes de forme : le libellé de la zone d'administration, et `_`.

* Le menu et l'action « Tous les courriels (admin) » se lisent sans tiret
  cadratin, dans les deux langues de la base.
* Aucune fonction d'un fichier qui importe `_` (la traduction) ne le rebinde
  en variable locale. `for _ in range(...)` ou `_, x = ...` masque la
  traduction dans TOUTE la fonction, et le premier `_("…")` qu'on y ajoute
  plus tard lève `TypeError` au lieu de traduire.
"""
import ast
from pathlib import Path

from odoo.tests import TransactionCase, tagged

RACINE = Path(__file__).resolve().parents[1]
LIBELLE = "Tous les courriels (admin)"


@tagged("post_install", "-at_install")
class TestLibellesEtTraduction(TransactionCase):

    def test_la_zone_admin_se_nomme_sans_tiret_cadratin(self):
        action = self.env.ref("bf_email_management.bf_email_action_admin_all")
        menu = self.env.ref("bf_email_management.menu_bf_email_admin_all")
        for lang in ("en_US", "fr_CA"):
            if not self.env["res.lang"]._lang_get(lang):
                continue
            with self.subTest(lang=lang):
                self.assertEqual(action.with_context(lang=lang).name, LIBELLE)
                self.assertEqual(menu.with_context(lang=lang).name, LIBELLE)

    def test_aucune_fonction_ne_masque_la_traduction(self):
        fautes = []
        for chemin in sorted(RACINE.rglob("*.py")):
            relatif = chemin.relative_to(RACINE)
            if relatif.parts[0] in ("tests", "migrations"):
                continue
            arbre = ast.parse(chemin.read_text(encoding="utf-8"))
            importe = any(
                isinstance(n, ast.ImportFrom)
                and any(a.name == "_" and a.asname is None for a in n.names)
                for n in ast.walk(arbre))
            if not importe:
                continue
            for fonction in ast.walk(arbre):
                if not isinstance(fonction, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for noeud in ast.walk(fonction):
                    if (isinstance(noeud, ast.Name) and noeud.id == "_"
                            and isinstance(noeud.ctx, ast.Store)):
                        fautes.append("%s:%s %s" % (relatif, noeud.lineno,
                                                    fonction.name))
        self.assertEqual(fautes, [], "`_` masqué en variable locale")
