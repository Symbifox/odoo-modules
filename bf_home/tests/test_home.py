# -*- coding: utf-8 -*-
"""The home screen has one job: never break the morning.

These tests do not assert what the numbers are, because that depends on which
modules a tenant has and what happened yesterday. They assert the contract the
client action relies on: the call succeeds, the shape is stable, empty bands are
absent rather than zeroed, and one broken collector cannot take the screen down.
"""

import ast
import inspect
import re
import textwrap
from datetime import datetime, time
from unittest.mock import patch

from odoo.addons.bf_home.models.bf_home import PYTHON_FILTERED, REQUIREMENTS
from odoo.addons.bf_home.models import bf_home as bf_home_module
from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestBfHome(TransactionCase):

    def test_no_collector_is_dormant_by_typo(self):
        """A quiet collector must mean "module absent", never "field misspelled".

        The two are indistinguishable at runtime, which is how four collectors
        once shipped silent: hour.bank.client.current_balance is not stored,
        hosting.service has last_health_status and not status, secure.transfer
        has expiry_date and not expiration_date, privacy.consent has expires_at
        and not next_reassessment_date. Each guard answered False, each band
        vanished, and nothing anywhere said so.
        """
        wrong = []
        for name, (model, needed) in sorted(REQUIREMENTS.items()):
            Model = self.env.get(model)
            if Model is None:
                continue          # tenant does not carry it: legitimate silence
            missing = [f for f in needed if f not in Model._fields]
            if missing:
                wrong.append("%s attend %s.%s" % (name, model, ", ".join(missing)))
        self.assertFalse(wrong, "collecteurs muets pour cause de champ inexistant :\n  "
                                + "\n  ".join(wrong))

    def test_declared_fields_are_searchable(self):
        """Declaring an unsearchable field is the same silent failure, one layer down.

        _has() answers True for a computed field, then the domain raises inside
        _safe() and the band disappears exactly as if the module were absent.
        Not-stored is not the test, though: a computed field with a search
        method goes in a domain perfectly well, which is how mail.message
        needaction works. What must never appear here is a field that is
        neither stored nor searchable — unless the collector is on the record
        as filtering it in Python.
        """
        unsearchable = []
        for name, (model, needed) in sorted(REQUIREMENTS.items()):
            Model = self.env.get(model)
            if Model is None or name in PYTHON_FILTERED:
                continue
            for f in needed:
                field = Model._fields.get(f)
                if field is not None and not field.store and not field.search:
                    unsearchable.append(
                        "%s déclare %s.%s : ni stocké, ni doté d'une méthode de recherche"
                        % (name, model, f))
        self.assertFalse(unsearchable, "\n  ".join(unsearchable))

    def test_declared_fields_are_used(self):
        """The third way a collector goes quiet: it declares a field and ignores it.

        The two tests above prove a declared field exists and can be searched.
        Neither notices when the collector never puts it in a domain — which is
        how _c_meetings declared partner_ids from the first version and showed
        every event on the tenant instead of the reader's own. On a
        single-operator tenant that renders correct output forever, so nothing
        was ever going to surface it at runtime.

        Reading the source is blunt, and deliberately so: the requirement is
        that the name appears in the collector at all, which is cheap to satisfy
        honestly and impossible to satisfy by accident.

        It reads *executable* source only. Grepping the raw text passed happily
        with the bug put back, because the docstring explaining the bug named
        the field — the check was scoring prose. Decorators go too, since @needs
        is where the name is declared and would let every collector vouch for
        itself; ast.unparse drops comments for the same reason.
        """
        unused = []
        for name, (model, needed) in sorted(REQUIREMENTS.items()):
            src = textwrap.dedent(inspect.getsource(getattr(bf_home_module.BfHome, name)))
            fn = ast.parse(src).body[0]
            stmts = fn.body
            if (stmts and isinstance(stmts[0], ast.Expr)
                    and isinstance(getattr(stmts[0], "value", None), ast.Constant)
                    and isinstance(stmts[0].value.value, str)):
                stmts = stmts[1:]                       # drop the docstring
            code = "\n".join(ast.unparse(n) for n in stmts)
            for f in needed:
                if not re.search(r"\b%s\b" % re.escape(f), code):
                    unused.append("%s déclare %s.%s sans jamais s'en servir" % (name, model, f))
        self.assertFalse(unused, "champs déclarés et inutilisés :\n  " + "\n  ".join(unused))

    def _banks_flagged(self):
        rows = self.env["bf.home"]._c_hour_banks()
        return set(rows[0]["domain"][0][2]) if rows else set()

    def test_an_hour_bank_is_judged_by_its_own_threshold(self):
        """The bank's configuration decides, not a floor this screen invented.

        Both fixtures have a zero balance, which the old flat ten-hour rule
        flagged unconditionally — that is how all four production banks ended up
        permanently red. One has alerting switched off, which is a decision to
        respect; the other declares a floor below zero, the postpaid shape, and
        a zero balance sits comfortably above it.
        """
        Bank = self.env.get("hour.bank.client")
        if Bank is None or "threshold_mode" not in Bank._fields:
            self.skipTest("ce locataire ne porte pas les banques d'heures")
        Partner = self.env["res.partner"]

        muted = Bank.create({
            "partner_id": Partner.create({"name": "Client alertes coupées"}).id,
            "threshold_mode": "disabled",
        })
        floored = Bank.create({
            "partner_id": Partner.create({"name": "Client compte postpayé"}).id,
            "threshold_mode": "balance_floor",
        })
        self.env["hour.bank.threshold.line"].create({"bank_id": floored.id, "value": -5.0})

        self.assertEqual(muted.current_balance, 0.0, "fixture : solde nul attendu")
        self.assertEqual(floored.current_balance, 0.0, "fixture : solde nul attendu")

        flagged = self._banks_flagged()
        self.assertNotIn(muted.id, flagged,
                         "une banque à alertes désactivées ne doit jamais remonter")
        self.assertNotIn(floored.id, flagged,
                         "un solde de 0 h au-dessus d'un plancher de -5 h n'est pas une alerte")

    def test_an_hour_bank_below_its_own_floor_is_flagged(self):
        """The other half of the same rule: silence must not be the new default."""
        Bank = self.env.get("hour.bank.client")
        if Bank is None or "threshold_mode" not in Bank._fields:
            self.skipTest("ce locataire ne porte pas les banques d'heures")
        bank = Bank.create({
            "partner_id": self.env["res.partner"].create({"name": "Client sous seuil"}).id,
            "threshold_mode": "balance_floor",
        })
        self.env["hour.bank.threshold.line"].create({"bank_id": bank.id, "value": 5.0})
        self.assertEqual(bank.current_balance, 0.0, "fixture : solde nul attendu")
        self.assertIn(bank.id, self._banks_flagged(),
                      "un solde de 0 h sous un plancher de 5 h doit remonter")

    def test_waiting_band_shows_one_row_per_client(self):
        """Four slots spent on one client tell you less than four spent on four."""
        Task = self.env.get("project.task")
        # Same tolerance the collectors have: this module installs on a tenant
        # without project, so its tests must survive one too.
        if Task is None or "05_waiting_client" not in dict(Task._fields["state"].selection or []):
            self.skipTest("ce locataire ne porte pas les états d'attente")
        Partner = self.env["res.partner"]
        project = self.env["project.project"].create({"name": "Projet du test bf_home"})
        # Three tasks on one client, one each on two others: the shape that used
        # to fill the band with a single name.
        for label, count in (("Client A", 3), ("Client B", 1), ("Client C", 1)):
            partner = Partner.create({"name": "%s (test bf_home)" % label})
            for i in range(count):
                Task.create({"name": "%s tâche %s" % (label, i), "project_id": project.id,
                             "partner_id": partner.id, "state": "05_waiting_client"})

        rows = self.env["bf.home"]._c_waiting()
        detail = [r for r in rows if r["res_id"]]
        self.assertTrue(detail, "la bande doit montrer au moins une tâche")

        keys = [Task.browse(r["res_id"]).partner_id.id for r in detail]
        self.assertEqual(len(keys), len(set(keys)),
                         "deux lignes de détail pointent le même dossier : %s" % keys)

        overflow = [r for r in rows if not r["res_id"]]
        self.assertEqual(len(overflow), 1,
                         "5 tâches pour 3 emplacements : il faut une ligne de débordement")
        self.assertTrue(overflow[0]["domain"],
                        "la ligne de débordement doit porter le domaine complet")
        self.assertTrue(Task.search_count(overflow[0]["domain"]) > len(detail),
                        "le domaine de débordement doit rouvrir toute la file")

    def _an_inbox_email(self, user=None, subject="Courriel du test bf_home"):
        """One row every reading of "boîte de réception" agrees is in it."""
        rec = self.env["bf.email"].create({
            "subject": subject,
            "direction": "in",
            "source": "imap",
            "imap_in_inbox": True,
            "date": fields.Datetime.now(),
            "user_id": (user or self.env.user).id,
        })
        # The rule engine runs on create and is allowed to file a row straight
        # out of the inbox. This test is about the domain, not about the rules a
        # tenant happens to carry, so put the row back where it belongs.
        rec.is_handled = False
        return rec

    def test_the_email_row_counts_the_readers_own_inbox(self):
        """The morning figure must be the badge's figure, not the office's.

        Operators in the "tous les courriels" group carry a (1=1) record rule,
        so a count without the owner leaf reads everyone's backlog as the
        reader's own: nine on the tenant this was decided against, where two
        were actually theirs.
        """
        if self.env.get("bf.email") is None:
            self.skipTest("ce locataire ne porte pas bf.email")
        other = self.env["res.users"].create({
            "name": "Autre propriétaire (test bf_home)",
            "login": "autre.proprietaire.test.bf.home",
        })
        mine = self._an_inbox_email(subject="À moi")
        theirs = self._an_inbox_email(other, subject="À quelqu'un d'autre")

        rows = self.env["bf.home"]._c_email()
        self.assertTrue(rows, "un courriel non traité doit produire une ligne")
        counted = self.env["bf.email"].search(rows[0]["domain"]).ids
        self.assertIn(mine.id, counted)
        self.assertNotIn(theirs.id, counted,
                         "la ligne compte la boîte de réception d'un autre")

    def test_the_email_row_speaks_the_tenants_own_inbox_vocabulary(self):
        """Four counters, one definition of "boîte de réception".

        The systray badge, the list action and the phone filter already had to
        agree on what the word means. A private copy of the domain in this
        screen is exactly how four counters end up counting four things, so the
        collector asks bf_email_management for the definition — and this test
        asserts it did not quietly fall back to its own.
        """
        Email = self.env.get("bf.email")
        if Email is None or not hasattr(Email, "_inbox_folder_defs"):
            self.skipTest("ce locataire ne porte pas la couche boîte de réception")
        canonical = next(d["domain"] for d in Email._inbox_folder_defs()
                         if d.get("key") == "inbox")
        self._an_inbox_email()

        rows = self.env["bf.home"]._c_email()
        self.assertTrue(rows, "un courriel non traité doit produire une ligne")
        domain = rows[0]["domain"]
        self.assertEqual(tuple(domain[0]), ("user_id", "=", self.env.uid),
                         "la feuille propriétaire doit venir en tête")
        self.assertEqual(list(domain[1:]), list(canonical),
                         "l'écran d'accueil et la boîte de réception ne comptent "
                         "plus la même chose")

    def test_day_bounds_cover_the_readers_day_not_the_servers(self):
        """The window must be the user's calendar day, converted to UTC.

        Read back through context_timestamp, the bounds have to land on the same
        date the reader would write down — which a naive date.today() dropped
        into a Datetime domain does not, by the tenant's UTC offset.
        """
        Home = self.env["bf.home"]
        start, end = Home._day_bounds()
        today = fields.Date.context_today(Home)
        for label, bound in (("début", start), ("fin", end)):
            local = fields.Datetime.context_timestamp(
                Home, fields.Datetime.to_datetime(bound)).date()
            self.assertEqual(local, today,
                             "la borne de %s tombe le %s, pas le %s" % (label, local, today))

    def test_returns_expected_shape(self):
        data = self.env["bf.home"].get_home_data()
        for key in ("user", "date", "headline", "bands", "panels", "all_clear"):
            self.assertIn(key, data, "clé manquante dans la charge utile : %s" % key)
        self.assertIsInstance(data["bands"], list)
        self.assertIsInstance(data["panels"], list)
        self.assertTrue(data["headline"], "la phrase du matin ne doit jamais être vide")

    def test_no_empty_band_is_returned(self):
        """An empty band disappears; it never renders as a reassuring zero."""
        for band in self.env["bf.home"].get_home_data()["bands"]:
            self.assertTrue(band["rows"], "la bande %s est vide et aurait dû disparaître" % band["key"])
            self.assertEqual(band["count"], len(band["rows"]))
            self.assertIn(band["sev"], ("crit", "warn", "calm"))

    def test_every_row_can_be_opened(self):
        """A figure without a way to reach its records is a dead statistic."""
        for band in self.env["bf.home"].get_home_data()["bands"]:
            for row in band["rows"]:
                self.assertTrue(row["title"])
                self.assertTrue(row["cta"], "ligne sans action : %s" % row["title"])
                self.assertTrue(row["model"], "ligne sans modèle à ouvrir : %s" % row["title"])
                self.assertIsInstance(row["domain"], list)

    def test_all_clear_is_consistent(self):
        data = self.env["bf.home"].get_home_data()
        self.assertEqual(data["all_clear"], not data["bands"])

    def test_a_broken_collector_does_not_break_the_screen(self):
        """The whole point of _safe(): a failing signal costs its band, not the page."""
        Home = self.env["bf.home"]

        def boom(self_):
            raise ValueError("collecteur cassé pour le test")

        with patch.object(type(Home), "_c_waiting", boom):
            data = Home.get_home_data()
        self.assertIn("bands", data)
        self.assertTrue(data["headline"])

    def test_a_prepared_meeting_is_not_amber(self):
        """Amber must mean "this one needs you", not "a meeting exists".

        Colour that fires on every meeting stops being read by the second week.
        Skipped on a tenant without the agenda model, where the collector has
        nothing to judge readiness with and stays amber by design.
        """
        Home = self.env["bf.home"]
        if not Home._has("calendar.event", "meeting_agenda_ids") \
           or "meeting.agenda" not in self.env:
            self.skipTest("ce locataire ne porte pas les ordres du jour")

        today = fields.Date.context_today(Home)
        ev = self.env["calendar.event"].create({
            "name": "Rencontre du test bf_home",
            "start": datetime.combine(today, time(14, 0)),
            "stop": datetime.combine(today, time(15, 0)),
        })

        def sev_for(event):
            return next((r["sev"] for r in Home._c_meetings() if r["res_id"] == event.id), None)

        self.assertEqual(sev_for(ev), "warn", "sans ordre du jour, la rencontre doit alerter")

        self.env["meeting.agenda"].create({
            "name": "Ordre du jour du test",
            "calendar_event_id": ev.id,
            "state": "confirmed",
            # project_id and meeting_type are NOT NULL on meeting.agenda
            "project_id": self.env["project.project"].create(
                {"name": "Projet du test bf_home"}).id,
            "meeting_type": "video",
        })
        ev.invalidate_recordset(["meeting_agenda_ids"])
        self.assertEqual(sev_for(ev), "calm",
                         "ordre du jour confirmé : la rencontre ne doit plus être ambre")

    def test_missing_model_is_tolerated(self):
        """_has() must answer False for a model this tenant does not carry."""
        Home = self.env["bf.home"]
        self.assertFalse(Home._has("model.qui.nexiste.pas"))
        self.assertFalse(Home._has("res.users", "champ_qui_nexiste_pas"))
        self.assertTrue(Home._has("res.users", "login"))
