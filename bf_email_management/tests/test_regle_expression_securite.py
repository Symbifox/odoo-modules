"""Correctif de sécurité 18.0.11.41.5 : les conditions « avancées » des règles.

Avant 41.5, l'expression d'une condition « Champ du contact » ou « Domaine
Odoo » recevait des RECORDSETS, et tout usager interne pouvait l'écrire. Ces
essais vérifient que la cause a disparu, sans rejouer d'attaque :

- l'évaluation ne reçoit plus aucun objet Odoo, seulement des valeurs simples ;
- un résultat qui n'est pas fait de valeurs simples est refusé ;
- seul un administrateur crée, modifie ou copie une condition avancée hors du
  catalogue du module, par quelque chemin que ce soit ;
- une condition avancée écrite par un non-administrateur n'est jamais évaluée ;
- les deux recettes du catalogue (client, fournisseur) rendent exactement ce
  qu'elles rendaient.
"""

import types
from unittest.mock import patch

from odoo.api import Environment
from odoo.exceptions import AccessError
from odoo.models import BaseModel
from odoo.tests import TransactionCase, tagged

from odoo.addons.bf_email_management.models import bf_email_rule_condition as cond_mod

CLIENT = "(p.customer_rank or 0) > 0"
FOURNISSEUR = "(p.supplier_rank or 0) > 0"
HORS_CATALOGUE = "(p.customer_rank or 0) > 5"
DOMAINE = "[('subject', '=', 'Objet d\\'essai')]"


def _valeurs_simples(valeur):
    """Vrai si ``valeur`` ne porte aucun objet Odoo, à aucune profondeur."""
    if isinstance(valeur, (BaseModel, Environment)):
        return False
    if isinstance(valeur, types.SimpleNamespace):
        return all(_valeurs_simples(v) for v in vars(valeur).values())
    if isinstance(valeur, (list, tuple, set, frozenset)):
        return all(_valeurs_simples(v) for v in valeur)
    if isinstance(valeur, dict):
        return all(_valeurs_simples(v) for v in valeur.values())
    return isinstance(valeur, (str, int, float, bool, type(None)))


@tagged("post_install", "-at_install", "regle_expression_securite")
class TestRegleExpressionSecurite(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Users = cls.env["res.users"].with_context(no_reset_password=True)
        cls.employe = Users.create({
            "name": "Employé Règles",
            "login": "regles.employe@test.invalid",
            "email": "employe@exemple.test",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
        })
        cls.admin_sys = Users.create({
            "name": "Admin Système",
            "login": "regles.admin@test.invalid",
            "email": "admin@exemple.test",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id,
                                  cls.env.ref("base.group_system").id])],
        })
        cls.compte = cls.env["bf.email.account"].create({
            "name": "Boîte règles sécurité",
            "user_id": cls.employe.id,
            "host": "imap.exemple.test",
            "port": 993,
            "login": "employe@exemple.test",
            "password": "x",
        })
        cls.env["bf.email.rule"].sudo().with_context(active_test=False).search(
            [("user_id", "=", cls.employe.id)]).unlink()
        Partner = cls.env["res.partner"]
        cls.client = Partner.create({"name": "Client", "customer_rank": 1})
        cls.fournisseur = Partner.create({"name": "Fournisseur", "supplier_rank": 1})
        cls.les_deux = Partner.create({"name": "Les deux", "customer_rank": 2, "supplier_rank": 3})
        cls.aucun = Partner.create({"name": "Aucun"})

    _numeros = iter(range(1, 10 ** 6))

    # ------------------------------------------------------------------
    def _regle(self, value, field_name="partner_field", env=None, user=None):
        env = env or self.env["bf.email.rule"].sudo().env
        return env["bf.email.rule"].create({
            "name": "Règle d'essai",
            "scope": "user",
            "user_id": (user or self.employe).id,
            "match_type": "all",
            "condition_ids": [(0, 0, {
                "kind": "condition", "field_name": field_name,
                "operator": "expr", "value": value,
            })],
        })

    def _courriel(self, partner=None, **extra):
        vals = {
            "date": "2026-09-23 10:00:00",
            "email_from": "Quelqu'un <quelquun@ailleurs.test>",
            "email_to": "employe@exemple.test",
            "subject": "Objet d'essai",
            "direction": "in",
            "source": "imap",
            "user_id": self.employe.id,
            "account_id": self.compte.id,
            "body_preview": "Corps",
            "raw_headers": "From: quelquun@ailleurs.test",
            "message_id_header": "<%s@test.invalid>" % next(self._numeros),
        }
        if partner:
            vals["partner_id"] = partner.id
        vals.update(extra)
        return self.env["bf.email"].sudo().create(vals)

    def _capturer(self):
        """Espionne safe_eval dans le module : garde chaque contexte reçu."""
        vus = []
        vrai = cond_mod.safe_eval

        def espion(expr, ctx=None, *a, **kw):
            vus.append(dict(ctx or {}))
            return vrai(expr, ctx, *a, **kw)
        return vus, patch.object(cond_mod, "safe_eval", side_effect=espion)

    # -- 1. aucun objet Odoo dans l'évaluation ---------------------------
    def test_le_contexte_champ_du_contact_ne_porte_aucun_objet_odoo(self):
        regle = self._regle(CLIENT)
        vus, espion = self._capturer()
        with espion:
            self.assertTrue(regle._match(self._courriel(self.client)))
        self.assertTrue(vus, "safe_eval n'a pas été appelé")
        for ctx in vus:
            self.assertTrue(_valeurs_simples(ctx), ctx)
            self.assertNotIn("record", ctx)
            self.assertNotIn("user", ctx)
            self.assertNotIn("env", ctx)

    def test_le_contexte_domaine_ne_porte_aucun_objet_odoo(self):
        regle = self._regle(DOMAINE, field_name="odoo_domain")
        vus, espion = self._capturer()
        with espion:
            self.assertTrue(regle._match(self._courriel()))
        self.assertTrue(vus, "safe_eval n'a pas été appelé")
        for ctx in vus:
            self.assertTrue(_valeurs_simples(ctx), ctx)
            self.assertEqual(set(ctx), {"uid"})

    def test_un_resultat_qui_nest_pas_fait_de_valeurs_simples_est_refuse(self):
        regle = self._regle(DOMAINE, field_name="odoo_domain")
        courriel = self._courriel()
        for resultat in (courriel, [courriel], [("id", "in", courriel)], object()):
            with patch.object(cond_mod, "safe_eval", return_value=resultat):
                self.assertFalse(regle.condition_ids._match_expression(courriel))

    def test_un_domaine_se_cherche_sous_le_proprietaire_de_la_ligne(self):
        regle = self._regle(DOMAINE, field_name="odoo_domain")
        courriel = self._courriel()
        uids = []
        Model = type(courriel)
        vrai = Model.search_count

        def compte(recs, domain, *a, **kw):
            uids.append(recs.env.uid)
            return vrai(recs, domain, *a, **kw)
        with patch.object(Model, "search_count", compte):
            self.assertTrue(regle.condition_ids._match_expression(courriel.sudo()))
        self.assertEqual(uids, [self.employe.id])

    # -- 2. seul un administrateur écrit une condition avancée ------------
    def test_un_employe_ne_cree_pas_de_condition_avancee_par_la_regle(self):
        Rule = self.env["bf.email.rule"].with_user(self.employe)
        for field_name, value in (("partner_field", HORS_CATALOGUE),
                                  ("odoo_domain", DOMAINE)):
            with self.assertRaises(AccessError):
                self._regle(value, field_name=field_name, env=Rule.env)

    def test_un_employe_ne_cree_pas_de_condition_avancee_directement(self):
        regle = self._regle(CLIENT)
        Cond = self.env["bf.email.rule.condition"].with_user(self.employe)
        with self.assertRaises(AccessError):
            Cond.create({"rule_id": regle.id, "kind": "condition",
                         "field_name": "partner_field", "operator": "expr",
                         "value": HORS_CATALOGUE})

    def test_un_employe_ne_rend_pas_avancee_une_condition_ordinaire(self):
        regle = self._regle(CLIENT)
        Cond = self.env["bf.email.rule.condition"].with_user(self.employe)
        ordinaire = Cond.create({"rule_id": regle.id, "kind": "condition",
                                 "field_name": "subject", "operator": "contains",
                                 "value": "facture"})
        with self.assertRaises(AccessError):
            ordinaire.write({"field_name": "partner_field", "operator": "expr",
                             "value": HORS_CATALOGUE})
        with self.assertRaises(AccessError):
            regle.with_user(self.employe).write({"condition_ids": [
                (1, ordinaire.id, {"field_name": "odoo_domain", "operator": "expr",
                                   "value": DOMAINE})]})

    def test_un_employe_ne_retouche_pas_une_condition_avancee_existante(self):
        regle = self._regle(HORS_CATALOGUE)  # écrite en superutilisateur
        cond = regle.condition_ids.with_user(self.employe)
        for vals in ({"value": "(p.customer_rank or 0) > 9"}, {"sequence": 99}):
            with self.assertRaises(AccessError):
                cond.write(vals)

    def test_un_employe_ne_copie_pas_une_condition_avancee(self):
        regle = self._regle(HORS_CATALOGUE)
        with self.assertRaises(AccessError):
            regle.condition_ids.with_user(self.employe).copy()
        with self.assertRaises(AccessError):
            regle.with_user(self.employe).copy()

    def test_un_employe_ne_charge_pas_de_condition_avancee_par_import(self):
        regle = self._regle(CLIENT)
        Cond = self.env["bf.email.rule.condition"].with_user(self.employe)
        resultat = Cond.load(
            ["rule_id/.id", "kind", "field_name", "operator", "value"],
            [[str(regle.id), "condition", "partner_field", "expr", HORS_CATALOGUE]])
        self.assertFalse([i for i in (resultat.get("ids") or []) if i])
        self.assertFalse(self.env["bf.email.rule.condition"].sudo().search(
            [("value", "=", HORS_CATALOGUE)]))

    def test_un_employe_garde_les_recettes_et_les_conditions_ordinaires(self):
        Rule = self.env["bf.email.rule"].with_user(self.employe)
        for recette in (CLIENT, FOURNISSEUR):
            regle = self._regle(recette, env=Rule.env)
            self.assertEqual(regle.condition_ids.value, recette)
        ordinaire = Rule.create({
            "name": "Ordinaire", "scope": "user", "user_id": self.employe.id,
            "match_type": "all",
            "condition_ids": [(0, 0, {"kind": "condition", "field_name": "subject",
                                      "operator": "contains", "value": "facture"})],
        })
        self.assertTrue(ordinaire.condition_ids)

    def test_un_administrateur_ecrit_une_condition_avancee(self):
        Rule = self.env["bf.email.rule"].with_user(self.admin_sys)
        regle = self._regle(HORS_CATALOGUE, env=Rule.env, user=self.admin_sys)
        regle.condition_ids.with_user(self.admin_sys).write(
            {"value": "(p.customer_rank or 0) > 1"})
        self.assertTrue(regle._match(self._courriel(self.les_deux)))

    # -- 3. une condition écrite par un non-administrateur n'est jamais évaluée
    def test_une_condition_avancee_dun_employe_nest_pas_evaluee(self):
        for champ in ("create_uid", "write_uid"):
            regle = self._regle("(p.customer_rank or 0) >= 0")
            cond = regle.condition_ids
            self.env.flush_all()  # sinon une écriture ORM en attente remet l'auteur après l'UPDATE
            self.env.cr.execute(
                "UPDATE bf_email_rule_condition SET %s = %%s WHERE id = %%s" % champ,
                (self.employe.id, cond.id))
            cond.invalidate_recordset()
            courriel = self._courriel(self.client)  # hors capture : l'ingestion joue d'autres règles
            vus, espion = self._capturer()
            with espion, self.assertLogs(cond_mod.__name__, level="WARNING"):
                self.assertFalse(cond._match_expression(courriel))
            self.assertFalse(vus, "l'expression a été évaluée (%s)" % champ)

    def test_une_recette_du_catalogue_reste_evaluee_quel_que_soit_lauteur(self):
        regle = self._regle(CLIENT)
        cond = regle.condition_ids
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE bf_email_rule_condition SET create_uid = %s, write_uid = %s WHERE id = %s",
            (self.employe.id, self.employe.id, cond.id))
        cond.invalidate_recordset()
        self.assertTrue(cond._match_expression(self._courriel(self.client)))

    # -- 4. les recettes du catalogue rendent ce qu'elles rendaient --------
    def test_les_deux_recettes_rendent_le_meme_resultat_quavant(self):
        attendu = {
            # contact : (client, fournisseur), la sémantique de 41.4
            "client": (True, False),
            "fournisseur": (False, True),
            "les_deux": (True, True),
            "aucun": (False, False),
        }
        regle_client = self._regle(CLIENT)
        regle_fourn = self._regle(FOURNISSEUR)
        for nom, (est_client, est_fourn) in attendu.items():
            courriel = self._courriel(getattr(self, nom))
            self.assertEqual(regle_client._match(courriel), est_client, nom)
            self.assertEqual(regle_fourn._match(courriel), est_fourn, nom)
        sans_contact = self._courriel(None, email_from="x@y.test",
                                      message_id_header="<sans-contact@test.invalid>")
        sans_contact.sudo().write({"partner_id": False, "author_id": False})
        self.assertFalse(regle_client._match(sans_contact))

    def test_le_catalogue_ne_porte_que_les_deux_recettes(self):
        self.assertEqual(cond_mod.CATALOGUE_EXPRESSIONS, frozenset({
            ("partner_field", CLIENT), ("partner_field", FOURNISSEUR)}))
        self.assertEqual(cond_mod.PARTNER_EXPRESSION_FIELDS,
                         ("customer_rank", "supplier_rank"))
