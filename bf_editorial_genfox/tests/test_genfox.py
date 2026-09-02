# -*- coding: utf-8 -*-
"""GenFox propose, un humain applique.

Les tests portent sur la frontière : ce qui est refusé, et ce qui est écrit
exactement. Le lancement réel est couvert par son refus (groupe, socket
absente) ; le seul test qui va plus loin neutralise `_dispatch` — ce pas
ouvre un fil détaché avec sa propre connexion à la base, hors de la
transaction du test, qu'il ne faut jamais laisser tourner ici. L'application
se teste sur une proposition posée à la main.
"""

import json
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.bf_editorial_genfox.models.suggestion import digest

FR = "<p>Le texte français d'origine, assez long pour compter.</p>"
EN = "<p>The original English text, long enough to count.</p>"


@tagged("post_install", "-at_install")
class TestGenFox(TransactionCase):

    def setUp(self):
        super().setUp()
        self.env.user.groups_id = [(4, self.env.ref(
            "bf_editorial.group_editorial_manager").id)]
        Lang = self.env["res.lang"]
        Lang._activate_lang("fr_CA")
        Lang._activate_lang("en_CA")
        self.fr = Lang.search([("code", "=", "fr_CA")], limit=1)
        self.en = Lang.search([("code", "=", "en_CA")], limit=1)
        self.blog = self.env["blog.blog"].create({"name": "Banc éditorial"})
        self.post = self.env["blog.post"].create({
            "name": "Billet d'essai", "blog_id": self.blog.id,
        })
        self._poser_creneaux(FR, EN)
        self.calendar = self.env["bf.editorial.calendar"].create({
            "name": "Flux GenFox", "require_all_langs": "no", "word_floor": 0,
        })
        self.entry = self.env["bf.editorial.entry"].create({
            "name": "Entrée d'essai", "calendar_id": self.calendar.id,
            "post_id": self.post.id,
        })
        self.env["bf.editorial.version"].create([
            {"entry_id": self.entry.id, "lang_id": self.fr.id, "is_source": True},
            {"entry_id": self.entry.id, "lang_id": self.en.id},
        ])
        self.entry.qa_state = "clean"

    def _poser_creneaux(self, fr, en):
        """Écrire les trois clés en SQL, comme la production les porte."""
        self.env.cr.execute(
            "UPDATE blog_post SET content = %s::jsonb WHERE id = %s",
            (json.dumps({"fr_CA": fr, "en_CA": en, "en_US": en}), self.post.id),
        )
        self.post.invalidate_recordset(["content"])

    def _slots(self):
        self.env.cr.execute(
            "SELECT content FROM blog_post WHERE id = %s", (self.post.id,))
        return dict(self.env.cr.fetchone()[0] or {})

    def _suggestion(self, **values):
        base = {
            "kind": "expand", "entry_id": self.entry.id,
            "calendar_id": self.calendar.id, "state": "done",
            "source_digest": digest(FR),
        }
        base.update(values)
        return self.env["bf.editorial.suggestion"].create(base)

    # ── Disponibilité ────────────────────────────────────────────────────
    def test_socket_absente_rend_indisponible(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_ai_bridge.socket", "/nulle/part/bridge.sock")
        self.entry.invalidate_recordset()
        self.assertFalse(self.entry.genfox_available,
                         "sans socket, le bouton ne doit pas s'afficher")
        self.assertFalse(self.calendar.genfox_available)

    def test_lancement_refuse_sans_pont(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_ai_bridge.socket", "/nulle/part/bridge.sock")
        with self.assertRaises(UserError) as caught:
            self.env["bf.editorial.suggestion"].launch("review", entry=self.entry)
        self.assertIn("bf_ai_bridge.socket", str(caught.exception))

    def test_lancement_refuse_a_la_redaction(self):
        redaction = self.env["res.users"].create({
            "name": "Rédaction", "login": "redaction-genfox@essai.invalid",
            "groups_id": [(6, 0, [
                self.env.ref("base.group_user").id,
                self.env.ref("bf_editorial.group_editorial_user").id,
            ])],
        })
        with self.assertRaises(UserError):
            self.env["bf.editorial.suggestion"].with_user(redaction).launch(
                "review", entry=self.entry)

    # ── Application ──────────────────────────────────────────────────────
    def test_refuse_si_l_article_a_bouge(self):
        suggestion = self._suggestion(proposed_fr="<p>Version étoffée.</p>")
        self._poser_creneaux("<p>Quelqu'un a réécrit entre-temps.</p>", EN)
        with self.assertRaises(UserError) as caught:
            suggestion.action_apply()
        self.assertIn("changé", str(caught.exception))

    def test_refuse_sans_texte(self):
        with self.assertRaises(UserError):
            self._suggestion().action_apply()

    def test_ecrit_le_seul_creneau_propose(self):
        """Le point le plus important : ne toucher que ce qui est proposé.

        Réécrire les trois clés d'un bloc effacerait les corrections faites
        dans l'autre langue entre le calcul et l'application.
        """
        suggestion = self._suggestion(proposed_fr="<p>Version étoffée.</p>")
        suggestion.action_apply()
        slots = self._slots()
        self.assertEqual(slots["fr_CA"], "<p>Version étoffée.</p>")
        self.assertEqual(slots["en_CA"], EN, "l'anglais ne devait pas bouger")
        self.assertEqual(slots["en_US"], EN)
        self.assertEqual(set(slots), {"fr_CA", "en_CA", "en_US"},
                         "aucune clé ne doit disparaître")

    def test_source_dans_en_us_est_suivie(self):
        """Un billet créé en contexte en_US n'a pas de clé fr_CA.

        Corriger le français sans toucher en_US laisserait la vraie source en
        arrière, et une traduction ultérieure remapperait l'ancien terme.
        """
        self.env.cr.execute(
            "UPDATE blog_post SET content = %s::jsonb WHERE id = %s",
            (json.dumps({"en_CA": EN, "en_US": FR}), self.post.id))
        self.post.invalidate_recordset(["content"])
        suggestion = self._suggestion(proposed_fr="<p>Version étoffée.</p>")
        suggestion.action_apply()
        slots = self._slots()
        self.assertEqual(slots["fr_CA"], "<p>Version étoffée.</p>")
        self.assertEqual(slots["en_US"], "<p>Version étoffée.</p>")

    def test_application_remet_la_qa_a_passer(self):
        """L'écriture est en SQL : le crochet ORM de bf_editorial ne la voit pas.

        Sans ce geste explicite, une QA verte d'avant l'écriture resterait
        affichée sur un texte qu'elle n'a jamais lu.
        """
        suggestion = self._suggestion(proposed_fr="<p>Version étoffée.</p>")
        suggestion.action_apply()
        self.assertEqual(self.entry.qa_state, "todo")
        self.assertTrue(suggestion.applied)
        self.assertTrue(suggestion.backup_json, "le retour arrière doit être gardé")
        self.assertEqual(json.loads(suggestion.backup_json)["fr_CA"], FR)

    def test_pas_de_seconde_application(self):
        suggestion = self._suggestion(proposed_fr="<p>Version étoffée.</p>")
        suggestion.action_apply()
        with self.assertRaises(UserError):
            suggestion.action_apply()

    def test_les_deux_creneaux_quand_les_deux_sont_proposes(self):
        suggestion = self._suggestion(
            proposed_fr="<p>Version étoffée.</p>",
            proposed_en="<p>Expanded version.</p>",
        )
        suggestion.action_apply()
        slots = self._slots()
        self.assertEqual(slots["fr_CA"], "<p>Version étoffée.</p>")
        self.assertEqual(slots["en_CA"], "<p>Expanded version.</p>")

    # ── Résolution des langues ───────────────────────────────────────────
    def test_les_langues_viennent_des_creneaux(self):
        """Pas de code de langue en dur : ce sont les créneaux qui décident."""
        from odoo.addons.bf_editorial_genfox.models.suggestion import lang_codes
        self.assertEqual(lang_codes(self.entry), ("fr_CA", "en_CA"))

    def test_langue_inactive_ne_fait_pas_planter_la_lecture(self):
        """Une langue déclarée mais désactivée levait « Invalid language code ».

        Le service ne doit pas tomber pour une histoire de paramétrage
        régional : on retombe sur la lecture par défaut.
        """
        self.fr.active = False
        self.entry.invalidate_recordset()
        self.assertIsInstance(self.entry._genfox_source_content(), str)

    def test_sans_creneaux_le_repli_tient(self):
        from odoo.addons.bf_editorial_genfox.models.suggestion import lang_codes
        orpheline = self.env["bf.editorial.entry"].create({
            "name": "Sans créneau", "calendar_id": self.calendar.id,
        })
        self.assertEqual(lang_codes(orpheline), ("fr_CA", "en_CA"))

    # ── Borne de fraîcheur ───────────────────────────────────────────────
    def _vieillir(self, suggestion, minutes):
        self.env.cr.execute(
            "UPDATE bf_editorial_suggestion "
            "SET create_date = now() - interval '%s minutes' WHERE id = %s",
            (minutes, suggestion.id))
        suggestion.invalidate_recordset()

    def test_passe_fraiche_est_en_cours(self):
        s = self._suggestion(kind="review", state="queued")
        self.assertTrue(s.in_progress)
        self.assertFalse(s.stalled)
        self.entry.invalidate_recordset()
        self.assertTrue(self.entry.genfox_pending)
        self.assertFalse(self.entry.genfox_stalled)
        self.assertTrue(self.entry.genfox_started)

    def test_passe_perdue_libere_les_boutons(self):
        """Le point de la borne : une passe tuée en vol ne doit pas geler l'entrée.

        Sans elle, un redémarrage du pont laissait « En cours » pour toujours,
        boutons cachés, et l'entrée devenait irrelançable sans passer par la
        base. Vécu le 2026-08-28 pendant la mise en service.
        """
        s = self._suggestion(kind="review", state="queued")
        self._vieillir(s, 45)
        self.assertFalse(s.in_progress)
        self.assertTrue(s.stalled)
        self.entry.invalidate_recordset()
        self.assertFalse(self.entry.genfox_pending,
                         "les boutons doivent redevenir disponibles")
        self.assertTrue(self.entry.genfox_stalled)

    def test_passe_rendue_n_est_ni_l_un_ni_l_autre(self):
        s = self._suggestion(kind="review", state="done")
        self._vieillir(s, 45)
        self.assertFalse(s.in_progress)
        self.assertFalse(s.stalled)

    def test_le_plafond_se_regle(self):
        s = self._suggestion(kind="review", state="queued")
        self._vieillir(s, 30)
        self.assertTrue(s.stalled, "30 min dépasse le défaut de 20")
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_editorial_genfox.stale_minutes", "90")
        s.invalidate_recordset()
        self.assertTrue(s.in_progress, "le plafond réglé à 90 min la rattrape")

    def test_plafond_illisible_retombe_sur_le_defaut(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_editorial_genfox.stale_minutes", "vingt")
        s = self._suggestion(kind="review", state="queued")
        self.assertTrue(s.in_progress, "un réglage illisible ne doit rien casser")

    # ── Retour du bouton ─────────────────────────────────────────────────
    def test_le_bouton_recharge_la_vue(self):
        """Sans rechargement, l'encadré bleu n'apparaît qu'après un F5.

        Le client garde la valeur de genfox_pending d'avant le clic : la passe
        part, et rien ne le montre. Vécu le 2026-08-29 sur l'entrée 57.
        """
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_ai_bridge.socket", "/nulle/part/bridge.sock")
        action = self.entry._notify("essai")
        suite = action["params"].get("next")
        self.assertTrue(suite, "la notification doit enchaîner un rechargement")
        self.assertEqual(suite["tag"], "soft_reload")

    # ── Fusion des deux boutons ──────────────────────────────────────────
    def test_bouton_unique_lance_une_passe_full(self):
        """Il n'y a plus qu'un geste : action_genfox_full lance kind='full'.

        `_dispatch` ouvre un fil détaché qui prend sa PROPRE connexion à la
        base, hors de la transaction du test : le laisser tourner écrirait
        pour de vrai sur le banc, en dehors de tout retour arrière. On le
        neutralise, ce qui suffit à prouver ce que ce test vérifie : la
        création d'une proposition de nature « full ».
        """
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_ai_bridge.socket", "/etc/hosts")
        Suggestion = self.env["bf.editorial.suggestion"]
        with patch.object(type(Suggestion), "_dispatch", lambda self: None):
            action = self.entry.action_genfox_full()
        self.assertEqual(action["tag"], "display_notification")
        suggestion = self.entry.suggestion_ids.sorted("id", reverse=True)[:1]
        self.assertEqual(suggestion.kind, "full")

    def test_action_genfox_full_refuse_sans_pont(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_ai_bridge.socket", "/nulle/part/bridge.sock")
        with self.assertRaises(UserError):
            self.entry.action_genfox_full()

    # ── Décision en attente ──────────────────────────────────────────────
    def test_proposition_rendue_avec_texte_est_en_attente(self):
        s = self._suggestion(kind="full", state="done",
                             proposed_fr="<p>Version étoffée.</p>")
        self.assertTrue(s.pending_decision)

    def test_revue_sans_texte_n_est_pas_en_attente(self):
        """Une lecture sans texte proposé ne réclame pas de décision : il n'y"""
        """a rien à accepter ni à écarter, juste à lire."""
        s = self._suggestion(kind="full", state="done", body_html="<p>Lu.</p>")
        self.assertFalse(s.pending_decision)

    def test_proposition_en_cours_n_est_pas_en_attente(self):
        s = self._suggestion(kind="full", state="queued",
                             proposed_fr="<p>Version étoffée.</p>")
        self.assertFalse(s.pending_decision)

    def test_proposition_appliquee_n_est_plus_en_attente(self):
        s = self._suggestion(kind="full", proposed_fr="<p>Version étoffée.</p>")
        s.action_apply()
        self.assertFalse(s.pending_decision)

    def test_entree_bleuit_quand_une_decision_attend(self):
        """Le signal que le bouton « Propositions GenFox » doit refléter."""
        self.assertFalse(self.entry.genfox_pending_decision)
        self._suggestion(kind="full", state="done",
                         proposed_fr="<p>Version étoffée.</p>")
        self.entry.invalidate_recordset()
        self.assertTrue(self.entry.genfox_pending_decision)

    def test_entree_redevient_grise_apres_application(self):
        s = self._suggestion(kind="full", proposed_fr="<p>Version étoffée.</p>")
        self.entry.invalidate_recordset()
        self.assertTrue(self.entry.genfox_pending_decision)
        s.action_apply()
        self.entry.invalidate_recordset()
        self.assertFalse(self.entry.genfox_pending_decision)

    def test_entree_redevient_grise_apres_ecart(self):
        s = self._suggestion(kind="full", state="done",
                             proposed_fr="<p>Version étoffée.</p>")
        self.entry.invalidate_recordset()
        self.assertTrue(self.entry.genfox_pending_decision)
        s.action_discard()
        self.entry.invalidate_recordset()
        self.assertFalse(self.entry.genfox_pending_decision)

    # ── Créneaux « traduits » automatiquement à l'application ────────────
    def test_application_marque_les_creneaux_ecrits_traduits(self):
        """Un créneau que GenFox vient d'écrire n'est plus « à traduire ».

        Vécu le 2026-08-29 : l'entrée 57 en production affichait « à traduire »
        sur ses deux langues juste après application d'un étoffement complet,
        alors que 2354 mots venaient d'y être écrits. L'état mentait.
        """
        suggestion = self._suggestion(
            proposed_fr="<p>Version étoffée.</p>",
            proposed_en="<p>Expanded version.</p>",
        )
        suggestion.action_apply()
        versions = {v.lang_code: v.state for v in self.entry.version_ids}
        self.assertEqual(versions, {"fr_CA": "translated", "en_CA": "translated"})

    def test_application_ne_marque_pas_relue(self):
        """Le pas qui reste humain, exprès : « Traduite », jamais « Relue ».

        Le calendrier du banc n'exige aucune langue (``require_all_langs``
        à « no », aucune ``lang_ids``) : ``langs_ready`` y vaudrait toujours
        vrai, quel que soit l'état des créneaux, et ne prouverait rien ici.
        On rejoue donc le calendrier exigeant de bf_editorial, sur cette seule
        entrée : c'est le seul contexte où « Traduite » et « Relue » diffèrent
        pour la garde.
        """
        self.entry.calendar_id.write({
            "require_all_langs": "yes",
            "lang_ids": [(6, 0, (self.fr | self.en).ids)],
        })
        suggestion = self._suggestion(
            proposed_fr="<p>Version étoffée.</p>",
            proposed_en="<p>Expanded version.</p>",
        )
        suggestion.action_apply()
        self.entry.invalidate_recordset()
        self.assertEqual(
            set(self.entry.version_ids.mapped("state")), {"translated"})
        self.assertFalse(self.entry.langs_ready,
                         "une écriture GenFox ne doit jamais ouvrir la garde"
                         " toute seule : il n'y a pas eu de relecture")

    def test_seul_le_creneau_ecrit_bouge(self):
        """proposed_en absent : le créneau anglais ne doit pas bouger."""
        suggestion = self._suggestion(proposed_fr="<p>Version étoffée.</p>")
        suggestion.action_apply()
        anglais = self.entry.version_ids.filtered(lambda v: v.lang_code == "en_CA")
        self.assertEqual(anglais.state, "todo")

    def test_creneau_deja_relu_retombe_a_traduite(self):
        """Une relecture d'avant ne portait pas sur le texte que GenFox vient
        d'écrire par-dessus : la garde ne doit pas rester ouverte à tort."""
        self.entry.version_ids.write({"state": "reviewed"})
        suggestion = self._suggestion(proposed_fr="<p>Version étoffée.</p>")
        suggestion.action_apply()
        source = self.entry.version_ids.filtered("is_source")
        self.assertEqual(source.state, "translated")

    def test_creneau_fantome_en_us_ne_leve_pas(self):
        """en_US n'a pas de fiche bf.editorial.version : rien à mettre à jour,
        et surtout pas d'erreur pour une clé qui n'existe pas côté module.

        La clé ``fr_CA`` doit être ABSENTE du jsonb, pas seulement vide, pour
        que l'écriture emprunte réellement la voie ``en_US`` : c'est ce que
        ``action_apply`` regarde (``code_source not in slots``), pas la
        vacuité de la valeur.
        """
        self.env.cr.execute(
            "UPDATE blog_post SET content = %s::jsonb WHERE id = %s",
            (json.dumps({"en_CA": EN, "en_US": FR}), self.post.id))
        self.post.invalidate_recordset(["content"])
        suggestion = self._suggestion(
            proposed_fr="<p>Version étoffée.</p>", source_digest=digest(FR))
        suggestion.action_apply()  # ne doit pas lever
        self.assertEqual(
            self.entry.version_ids.filtered("is_source").state, "translated")
