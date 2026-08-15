# -*- coding: utf-8 -*-
"""Signal collector for the Symbifox home screen.

Design constraints that shaped this file:

* **No hard dependency on the modules it reads.** The manifest depends on
  ``base``, ``web`` and ``mail`` only. Every collector declares what it needs
  through ``@needs`` and is skipped when the tenant does not carry it, so this
  installs and runs on a tenant that has three of the modules or all thirteen.
  A band with nothing to say simply does not appear, which is also the design
  rule for the screen itself.
* **One broken collector must never take down the screen.** Each is wrapped;
  a failure logs and yields nothing rather than raising into the client action.
* **Silence must be provable.** Those first two rules make a typo in a field
  name indistinguishable from a module the tenant does not have: the collector
  goes quiet either way. ``@needs`` publishes every requirement in
  ``REQUIREMENTS`` so the test suite can assert that a model which *is*
  installed carries every field we claim, and ``_diagnose()`` answers the same
  question from a shell. Four collectors shipped dormant before this existed.
* **Every figure carries the action that resolves it.** A row without a domain
  to open is a dead statistic, and the screen has no room for those.
"""

import functools
import logging
from datetime import datetime, time, timedelta

import pytz

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

CRIT, WARN, CALM = "crit", "warn", "calm"

#: collector name -> (model, (field, ...)). Populated by @needs at import time
#: and read back by the test suite; see the module docstring.
REQUIREMENTS = {}

#: Collectors that knowingly declare a field they cannot put in a domain, and
#: filter it in Python instead. Declared here rather than hidden in the test, so
#: the exemption is a decision on the record and not a special case nobody sees.
PYTHON_FILTERED = {"_c_hour_banks"}


def needs(model, *fields_needed):
    """Declare the model and fields a collector reads, and guard on them.

    The declaration is the point: a guard written inline inside each collector
    works just as well at runtime but tells nobody, afterwards, what the
    collector was supposed to find.
    """
    def deco(fn):
        REQUIREMENTS[fn.__name__] = (model, fields_needed)

        @functools.wraps(fn)
        def wrapper(self):
            if not self._has(model, *fields_needed):
                return []
            return fn(self)

        return wrapper
    return deco


class BfHome(models.AbstractModel):
    _name = "bf.home"
    _description = "Écran d'accueil Symbifox"

    # ------------------------------------------------------------------ utils
    @api.model
    def _has(self, model, *fields_needed):
        """True when the model exists on this tenant and carries every field.

        Guards against both "module not installed" and "installed but an older
        version without this field", which is the case that actually bites when
        tenants upgrade at different times.
        """
        Model = self.env.get(model)
        if Model is None:
            return False
        try:
            return all(f in Model._fields for f in fields_needed)
        except Exception:  # noqa: BLE001 - a broken model must not break the page
            return False

    @api.model
    def _diagnose(self):
        """Why a band is quiet: absent module, missing field, or nothing to say.

        Underscore-prefixed on purpose - this is a shell affordance, not RPC
        surface.
        """
        out = []
        for name, (model, flds) in sorted(REQUIREMENTS.items()):
            Model = self.env.get(model)
            if Model is None:
                out.append((name, model, "module absent"))
                continue
            missing = [f for f in flds if f not in Model._fields]
            out.append((name, model,
                        "champ absent : %s" % ", ".join(missing) if missing else "actif"))
        return out

    @api.model
    def _row(self, sev, icon, title, sub, age, cta, model=None, domain=None, res_id=None):
        return {
            "sev": sev, "icon": icon, "title": title, "sub": sub,
            "age": age, "cta": cta, "model": model or False,
            # False, not None: Odoo's own convention, and it keeps the payload
            # marshalable over XML-RPC, which is how this gets verified on a
            # production tenant where session auth by API key is refused.
            "domain": domain or [], "res_id": res_id or False,
        }

    def _day_bounds(self, day=None):
        """A calendar day in the reader's timezone, as the UTC the ORM compares to.

        Datetime columns are stored and filtered in UTC. Building the window from
        a naive ``date.today()`` therefore asked for a *UTC* day, which in
        Montreal starts at 20:00 the previous evening: an 8 p.m. meeting never
        appeared on the day it happened, then turned up the next morning labelled
        "aujourd'hui". The display side already went through
        ``context_timestamp``, which is exactly what made the mismatch invisible.
        """
        day = day or fields.Date.context_today(self)
        tz = pytz.timezone(self.env.context.get("tz") or self.env.user.tz or "UTC")
        first = tz.localize(datetime.combine(day, time.min)).astimezone(pytz.utc)
        last = tz.localize(datetime.combine(day, time.max)).astimezone(pytz.utc)
        return (fields.Datetime.to_string(first.replace(tzinfo=None)),
                fields.Datetime.to_string(last.replace(tzinfo=None)))

    def _days_since(self, value):
        """Age in days, counted between the reader's calendar days, not the server's."""
        if not value:
            return 0
        if isinstance(value, datetime):        # a datetime is naive UTC out of the ORM
            value = fields.Datetime.context_timestamp(self, value).date()
        return (fields.Date.context_today(self) - value).days

    @staticmethod
    def _pct(part, whole):
        return int(round(100.0 * part / whole)) if whole else 0

    def _safe(self, fn):
        try:
            return fn() or []
        except Exception:  # noqa: BLE001
            _logger.exception("bf_home: collector %s failed", getattr(fn, "__name__", fn))
            return []

    # -------------------------------------------------------------- collectors
    @needs("mail.activity", "date_deadline", "user_id")
    def _c_activities(self):
        """Overdue activities assigned to me."""
        Act = self.env["mail.activity"]
        # Due today counts as much as overdue: an activity planned for this
        # morning is the work, not a warning about the past.
        dom = [("user_id", "=", self.env.uid),
               ("date_deadline", "<=", fields.Date.context_today(self))]
        n = Act.search_count(dom)
        if not n:
            return []
        oldest = Act.search(dom, order="date_deadline asc", limit=1)
        late = self._days_since(oldest.date_deadline)
        return [self._row(
            CRIT if late >= 7 else WARN, "bf_bloc_notes",
            _("%s activité(s) à traiter") % n,
            _("La plus vieille remonte à %s jours") % late if late
            else _("Planifiée pour aujourd'hui"),
            str(n), _("Traiter"), "mail.activity", dom)]

    @needs("calendar.event", "start", "partner_ids")
    def _c_meetings(self):
        """Today's meetings, coloured by whether they are actually prepared.

        A meeting with a confirmed or sent agenda needs nothing from you, so it
        reads calm. Amber is reserved for the ones still missing one — otherwise
        the colour says "attention" on a morning where nothing is wrong, and
        stops meaning anything by the second week.

        Scoped to meetings the reader actually attends. ``partner_ids`` was
        declared here from the first version and never put in the domain, so the
        band showed every event on the tenant. On a single-operator tenant that
        reads correct and stays wrong; the guard apparatus above catches a
        misspelled field and an unsearchable one, but not a declared field that
        no domain ever uses. ``test_declared_fields_are_used`` now does.
        """
        start, end = self._day_bounds()
        dom = [("start", ">=", start), ("start", "<=", end),
               ("partner_ids", "in", self.env.user.partner_id.ids)]
        events = self.env["calendar.event"].search(dom, order="start asc", limit=4)
        knows_agendas = self._has("calendar.event", "meeting_agenda_ids")
        rows = []
        for ev in events:
            when = _("Aujourd'hui à %s") % fields.Datetime.context_timestamp(
                self, ev.start).strftime("%-H h %M")
            ready, why = True, when
            if knows_agendas:
                skipped = "bf_skip_agenda" in ev._fields and ev.bf_skip_agenda
                prepared = any(
                    a.sent_date or a.state in ("confirmed", "done")
                    for a in ev.meeting_agenda_ids
                )
                ready = bool(skipped or prepared)
                why = "%s · %s" % (when, _("ordre du jour prêt") if ready
                                   else _("aucun ordre du jour"))
            rows.append(self._row(
                CALM if ready else WARN, "bf_meeting", ev.name or _("Rencontre"),
                why, "", _("Ouvrir") if ready else _("Préparer"),
                "calendar.event", [("id", "=", ev.id)], ev.id))
        return rows

    @needs("meeting.record", "report_state", "date")
    def _c_meeting_reports(self):
        """Reports written but never sent.

        A successful send leaves no trace by design, so the only reliable
        reading of "has it gone out" is the state on the record itself. Bounded
        to a recent window: a two-year-old draft is history, not a task.
        """
        Rec = self.env["meeting.record"]
        states = dict(Rec._fields["report_state"].selection or [])
        unsent = [s for s in ("draft", "reviewed") if s in states]
        if not unsent:
            return []
        since, _end = self._day_bounds(fields.Date.context_today(self) - timedelta(days=45))
        dom = [("report_state", "in", unsent), ("date", ">=", since)]
        recs = Rec.search(dom, order="date asc")
        if not recs:
            return []
        d = self._days_since(recs[0].date)
        return [self._row(
            CRIT if d >= 7 else WARN, "bf_meeting",
            _("%s compte(s) rendu non transmis") % len(recs),
            recs[0].display_name or "", _("depuis %s j") % d,
            _("Transmettre"), "meeting.record", dom)]

    @needs("account.analytic.line", "date", "user_id", "unit_amount")
    def _c_timesheet_gap(self):
        """Yesterday had no timesheet line."""
        y = fields.Date.context_today(self) - timedelta(days=1)
        if y.weekday() >= 5:                      # nobody owes a Saturday
            return []
        dom = [("user_id", "=", self.env.uid), ("date", "=", y)]
        hours = sum(self.env["account.analytic.line"].search(dom).mapped("unit_amount"))
        if hours:
            return []
        return [self._row(
            CALM, "bf_timesheet_timer", _("Aucune heure saisie hier"),
            _("Rien au %s") % y.strftime("%d/%m"), "0 h", _("Saisir"),
            "account.analytic.line", [("user_id", "=", self.env.uid)])]

    @needs("project.task", "date_deadline", "user_ids", "state")
    def _c_tasks(self):
        """My tasks, split so the backlog cannot drown today.

        Listing every overdue task would put 253 rows on a screen meant to be
        read in ten seconds. Today's deadlines are the work; everything older is
        one summary line that opens the full list when you want it.
        """
        Task = self.env["project.task"]
        closed = [s for s in ("1_done", "1_canceled", "1_cancelled")
                  if s in dict(Task._fields["state"].selection or [])]
        mine = [("user_ids", "in", [self.env.uid]), ("state", "not in", closed)]
        start, end = self._day_bounds()

        rows = []
        due = mine + [("date_deadline", ">=", start), ("date_deadline", "<=", end)]
        n_due = Task.search_count(due)
        if n_due:
            first = Task.search(due, order="priority desc, id asc", limit=1)
            rows.append(self._row(
                WARN, "project", _("%s tâche(s) à échéance aujourd'hui") % n_due,
                first.name or "", str(n_due), _("Ouvrir"), "project.task", due))

        late = mine + [("date_deadline", "<", start), ("date_deadline", "!=", False)]
        n_late = Task.search_count(late)
        if n_late:
            oldest = Task.search(late, order="date_deadline asc", limit=1)
            when = fields.Datetime.context_timestamp(self, oldest.date_deadline)
            rows.append(self._row(
                CALM, "project", _("%s tâches dont l'échéance est passée") % n_late,
                _("La plus ancienne remonte au %s") % when.strftime("%d/%m/%Y"),
                str(n_late), _("Trier"), "project.task", late))
        return rows

    @needs("mail.message", "needaction")
    def _c_inbox(self):
        """Odoo's own inbox: messages addressed to me and still unread."""
        n = self.env["mail.message"].search_count([("needaction", "=", True)])
        if not n:
            return []
        return [self._row(
            WARN, "mail", _("%s message(s) dans la boîte de réception") % n,
            _("Mentions et suivis qui vous sont adressés"), str(n),
            _("Lire"), "mail.message", [("needaction", "=", True)])]

    #: Distinct clients shown in the waiting band before the overflow row.
    WAITING_CLIENTS = 3

    @needs("project.task", "state")
    def _c_waiting(self):
        """Tasks parked on somebody else. The band nobody else surfaces.

        One row per client, then a row for everything that did not fit. Taking
        the three oldest per state instead put the same client in three of four
        slots — dozens of tasks across a dozen clients reduced to one name
        repeated — and the rest had no route off this screen at all. Picking the oldest
        task *per client* keeps the ranking honest and spends each slot on a
        different conversation; the overflow row carries the full domain, so the
        rest is one click away instead of invisible.
        """
        Task = self.env["project.task"]
        states = dict(Task._fields["state"].selection or [])
        waiting = [s for s in ("05_waiting_client", "06_waiting_external") if s in states]
        if not waiting:
            return []
        dom = [("state", "in", waiting)]
        total = Task.search_count(dom)
        if not total:
            return []

        rows, seen = [], set()
        # Bounded: enough to find WAITING_CLIENTS distinct clients in any
        # realistic spread, without reading a backlog of unknown size.
        for t in Task.search(dom, order="write_date asc", limit=80):
            # Tasks with no client key on their project, so two unrelated
            # internal projects do not collapse into a single "(sans client)".
            key = t.partner_id.id or ("project", t.project_id.id)
            if key in seen:
                continue
            seen.add(key)
            d = self._days_since(t.write_date)
            rows.append(self._row(
                CRIT if d >= 10 else WARN, "bf_helpdesk", t.name,
                "%s · %s" % (t.partner_id.display_name or t.project_id.display_name or "",
                             states[t.state]),
                _("depuis %s j") % d, _("Relancer"), "project.task",
                [("id", "=", t.id)], t.id))
            if len(rows) >= self.WAITING_CLIENTS:
                break

        rows = sorted(rows, key=lambda r: r["sev"] != CRIT)
        rest = total - len(rows)
        if rest > 0:
            # Counted over the whole domain, not over the rows shown: a subtitle
            # that says "3 dossiers" because three rows fit is not a fact.
            clients = len(Task._read_group(dom, ["partner_id"]))
            rows.append(self._row(
                CALM, "bf_helpdesk", _("%s autres tâches en attente") % rest,
                _("Réparties sur %s dossiers") % clients if clients > 1 else "",
                str(rest), _("Tout voir"), "project.task", dom))
        return rows

    @needs("bf.sign.request", "state")
    def _c_signatures(self):
        """Signature requests sent and still unsigned."""
        Req = self.env["bf.sign.request"]
        states = dict(Req._fields["state"].selection or [])
        pending = [s for s in ("sent", "in_progress", "pending") if s in states]
        if not pending:
            return []
        dom = [("state", "in", pending)]
        n = Req.search_count(dom)
        if not n:
            return []
        oldest = Req.search(dom, order="create_date asc", limit=1)
        d = self._days_since(oldest.create_date)
        return [self._row(
            CRIT if d >= 7 else WARN, "bf_sign",
            _("%s signature(s) en attente") % n,
            oldest.display_name or "", _("depuis %s j") % d,
            _("Renvoyer"), "bf.sign.request", dom)]

    @needs("secure.transfer", "expiry_date", "state")
    def _c_transfers(self):
        """Live deposits about to expire without being collected."""
        Tr = self.env["secure.transfer"]
        _first, soon = self._day_bounds(fields.Date.context_today(self) + timedelta(days=3))
        dom = [("state", "=", "active"), ("expiry_date", "<=", soon),
               ("expiry_date", ">=", fields.Datetime.now())]
        n = Tr.search_count(dom)
        if not n:
            return []
        return [self._row(
            CRIT, "bf_securetransfer", _("%s dépôt(s) expirent bientôt") % n,
            _("Le lien deviendra inutilisable"), _("≤ 3 j"),
            _("Prolonger"), "secure.transfer", dom)]

    @needs("bf.outreach.target", "next_action_date")
    def _c_outreach(self):
        dom = [("next_action_date", "<=", fields.Date.context_today(self))]
        n = self.env["bf.outreach.target"].search_count(dom)
        if not n:
            return []
        return [self._row(
            CALM, "bf_outreach", _("%s cibles à relancer") % n,
            _("Cadence atteinte"), str(n), _("Ouvrir"), "bf.outreach.target", dom)]

    #: Floor used only for a bank that alerts but gives no readable balance
    #: threshold of its own. Not a policy — a last resort, and it says so.
    DEFAULT_BANK_FLOOR = 10.0

    @needs("hour.bank.client", "current_balance")
    def _c_hour_banks(self):
        """Hour banks under the threshold *they* declare, not one this screen invents.

        ``bf_hour_bank`` already carries the decision: ``threshold_mode`` per
        bank, and ``balance_floor`` lines holding the hours at which that client
        should be looked at (−12 h on a postpaid account, 5,2 h on a prepaid
        one). A flat ten-hour floor here ignored all of it and fired on four
        banks out of four, two of which have alerting explicitly *disabled* —
        so the band was permanently red, which is the same as being off, and it
        overruled a setting somebody had chosen on purpose.

        Modes other than ``balance_floor`` (``budget_pct``, ``unbilled``) count
        something this row cannot render as a balance. Rather than drop those
        banks into silence — the failure this module exists to prevent — they
        keep the default floor, and the subtitle says which reading applied.
        """
        Bank = self.env["hour.bank.client"]
        # current_balance is computed and NOT stored, so it cannot go in a
        # domain. Read the banks and filter in Python; the set is small by
        # nature, and the resulting domain resolves by id.
        banks = Bank.search([("active", "=", True)] if "active" in Bank._fields else [])
        knows_thresholds = self._has("hour.bank.client", "threshold_mode", "threshold_line_ids")

        low, defaulted = self.env["hour.bank.client"], False
        for bank in banks:
            floor, own = self.DEFAULT_BANK_FLOOR, False
            if knows_thresholds:
                if bank.threshold_mode == "disabled":
                    continue                  # the operator opted out; respect it
                floors = bank.threshold_line_ids.filtered(
                    lambda line: line.active and line.mode == "balance_floor").mapped("value")
                if floors:
                    # Highest floor = the first tier the balance crosses on its
                    # way down, which is the one worth warning about.
                    floor, own = max(floors), True
            if (bank.current_balance or 0) <= floor:
                low |= bank
                defaulted = defaulted or not own
        if not low:
            return []

        names = ", ".join(low[:2].mapped("display_name"))
        age = _("sous le seuil configuré") if not defaulted else _("≤ %g h") % self.DEFAULT_BANK_FLOOR
        return [self._row(
            CRIT, "bf_hour_bank", _("%s banque(s) d'heures à surveiller") % len(low),
            names, age, _("Facturer"), "hour.bank.client", [("id", "in", low.ids)])]

    # _c_unbilled removed in 18.0.1.1.0. It counted analytic lines with no
    # timesheet_invoice_id, which reads as "hours nobody has billed" only on a
    # tenant that bills *through* Odoo's timesheet-invoicing link. Where billing
    # runs on hour banks instead, that field is never set: every line over the
    # window matched, the row printed the whole timesheet as if it were a debt,
    # and the band it lived in was permanently amber because of it. A warning
    # that fires on all of the data is a label, not a signal. The hour-bank
    # collector above is the honest version of the same question. Restoring this
    # row means first finding the field that separates billed from unbilled here.

    @needs("privacy.consent", "expires_at", "active")
    def _c_privacy(self):
        """Consents about to lapse. A lapsed consent is a compliance hole."""
        _first, soon = self._day_bounds(fields.Date.context_today(self) + timedelta(days=30))
        dom = [("active", "=", True), ("expires_at", "!=", False),
               ("expires_at", "<=", soon)]
        n = self.env["privacy.consent"].search_count(dom)
        if not n:
            return []
        return [self._row(
            WARN, "privacy_consent", _("%s consentement(s) à renouveler") % n,
            _("Échéance dans les 30 jours"), str(n),
            _("Réviser"), "privacy.consent", dom)]

    @needs("hosting.service", "last_health_status", "state")
    def _c_hosting(self):
        Svc = self.env["hosting.service"]
        dom = [("state", "=", "active"),
               ("last_health_status", "in", ["down", "timeout", "degraded"])]
        n = Svc.search_count(dom)
        if not n:
            return []
        worst = Svc.search(dom, limit=2)
        return [self._row(
            CRIT, "hosting_management", _("%s service(s) en difficulté") % n,
            ", ".join(worst.mapped("name")), str(n),
            _("Diagnostiquer"), "hosting.service", dom)]

    @needs("bf.cx.feedback", "score")
    def _c_cx(self):
        Fb = self.env["bf.cx.feedback"]
        dom = [("score", "<=", 6), ("score", ">", 0)]
        if "state" in Fb._fields:
            states = dict(Fb._fields["state"].selection or [])
            open_states = [s for s in states if s not in ("done", "closed", "resolved")]
            if open_states:
                dom.append(("state", "in", open_states))
        n = Fb.search_count(dom)
        if not n:
            return []
        return [self._row(
            CALM, "bf_cx", _("%s détracteur(s) sans suivi") % n,
            _("Note de 6 ou moins, boucle ouverte"), str(n),
            _("Rappeler"), "bf.cx.feedback", dom)]

    # ------------------------------------------------------------------ panels
    # The three dashboards this screen replaces, reduced to the figures a person
    # actually reads on a Tuesday. Deliberately not the whole dashboard: what is
    # kept here is background, and anything that demands an action belongs in a
    # band above, where it can be clicked and resolved.

    def _p_meetings(self):
        if not self._has("meeting.record", "date", "report_state"):
            return []
        Rec = self.env["meeting.record"]
        total = Rec.search_count([])
        if not total:
            return []
        first_of_month, _end = self._day_bounds(
            fields.Date.context_today(self).replace(day=1))
        month = Rec.search_count([("date", ">=", first_of_month)])
        sent = Rec.search_count([("report_state", "=", "sent")])
        pct = self._pct(sent, total)
        return [{
            "icon": "bf_meeting", "title": _("Rencontres"), "model": "meeting.record",
            "stats": [
                {"k": _("Consignées"), "v": total},
                {"k": _("Ce mois-ci"), "v": month},
                {"k": _("Comptes rendus transmis"), "v": "%s %%" % pct, "meter": pct},
            ],
        }]

    def _p_knowledge(self):
        stats = []
        if self._has("project.document", "active", "is_review_due"):
            Doc = self.env["project.document"]
            stats.append({"k": _("Documents actifs"),
                          "v": Doc.search_count([("active", "=", True)])})
            stats.append({"k": _("Révisions en retard"),
                          "v": Doc.search_count([("active", "=", True),
                                                 ("is_review_due", "=", True)])})
        if self._has("project.credential", "state"):
            stats.append({"k": _("Identifiants à renouveler"),
                          "v": self.env["project.credential"].search_count(
                              [("state", "in", ["expiring", "expired"])])})
        if self._has("project.knowledge.matrix", "completed_count", "item_count"):
            Mat = self.env["project.knowledge.matrix"]
            dom = [("is_template", "=", False)] if "is_template" in Mat._fields else []
            mats = Mat.search(dom)
            items = sum(mats.mapped("item_count"))
            if items:
                pct = self._pct(sum(mats.mapped("completed_count")), items)
                stats.append({"k": _("Avancement des matrices"), "v": "%s %%" % pct,
                              "meter": pct})
        if not stats:
            return []
        model = "project.document" if self._has("project.document", "active") \
            else "project.knowledge.matrix"
        return [{"icon": "project_knowledge_matrix", "title": _("Connaissances"),
                 "model": model, "stats": stats}]

    def _p_hosting(self):
        if not self._has("hosting.service", "last_health_status", "state"):
            return []
        Svc = self.env["hosting.service"]
        base = [("state", "=", "active"), ("last_health_status", "!=", False)]
        watched = Svc.search_count(base)
        if not watched:
            return []
        # Availability is read from the stored verdict on each service, never by
        # aggregating hosting.health.check: that table carries over a million
        # rows and this screen loads on every login.
        pct = self._pct(Svc.search_count(base + [("last_health_status", "=", "up")]), watched)
        stats = [
            {"k": _("Disponibilité"), "v": "%s %%" % pct, "meter": pct},
            {"k": _("Services surveillés"), "v": watched},
        ]
        if "update_available" in Svc._fields:
            stats.append({"k": _("Mises à jour disponibles"),
                          "v": Svc.search_count([("state", "=", "active"),
                                                 ("update_available", "=", True)])})
        if self._has("hosting.maintenance.schedule", "next_due"):
            # Compared against next_due rather than the is_overdue flag: that
            # flag is a stored computed field that is not refreshed daily, so it
            # still reads False on schedules whose date passed days ago. The
            # date is the fact; the flag is a cache of it.
            Sched = self.env["hosting.maintenance.schedule"]
            dom = [("next_due", "<", fields.Date.context_today(self)),
                   ("next_due", "!=", False)]
            if "active" in Sched._fields:
                dom.append(("active", "=", True))
            stats.append({"k": _("Maintenances en retard"), "v": Sched.search_count(dom)})
        if self._has("hosting.backup.run", "run_date", "state"):
            last = self.env["hosting.backup.run"].search([], order="run_date desc", limit=1)
            if last and last.run_date:
                label = dict(last._fields["state"].selection or []).get(last.state, last.state)
                stats.append({"k": _("Dernière sauvegarde"),
                              "v": "%s · %s" % (last.run_date.strftime("%d/%m"), label)})
        return [{"icon": "hosting_management", "title": _("Hébergement"),
                 "model": "hosting.service", "stats": stats}]

    # -------------------------------------------------------------------- main
    @api.model
    def get_home_data(self):
        bands = [
            {"key": "today", "title": _("Ce qui m'attend"), "q": _("aujourd'hui"),
             "rows": self._safe(self._c_meetings) + self._safe(self._c_activities)
                     + self._safe(self._c_tasks) + self._safe(self._c_inbox)
                     + self._safe(self._c_meeting_reports) + self._safe(self._c_timesheet_gap)},
            {"key": "them", "title": _("En attente d'eux"),
             "q": _("la balle n'est pas dans votre camp"),
             "rows": self._safe(self._c_waiting) + self._safe(self._c_signatures)
                     + self._safe(self._c_transfers) + self._safe(self._c_outreach)},
            {"key": "money", "title": _("L'argent"), "q": _("ce qui fuit si personne ne regarde"),
             "rows": self._safe(self._c_hour_banks)},
            {"key": "risk", "title": _("Le risque"), "q": _("conformité et exploitation"),
             "rows": self._safe(self._c_privacy) + self._safe(self._c_hosting)
                     + self._safe(self._c_cx)},
        ]
        # An empty band disappears. A quiet morning gives a short screen, not a
        # wall of reassuring zeros nobody reads after the first week.
        bands = [b for b in bands if b["rows"]]
        for b in bands:
            sev = {r["sev"] for r in b["rows"]}
            b["sev"] = CRIT if CRIT in sev else (WARN if WARN in sev else CALM)
            b["count"] = len(b["rows"])

        panels = (self._safe(self._p_meetings) + self._safe(self._p_knowledge)
                  + self._safe(self._p_hosting))

        return {
            "user": self.env.user.name.split(" ")[0],
            "date": fields.Date.context_today(self).strftime("%d/%m/%Y"),
            "headline": self._headline(bands),
            "bands": bands,
            "panels": panels,
            "all_clear": not bands,
        }

    def _headline(self, bands):
        """One sentence naming the single most important thing.

        Deliberately not a chat box: GenFox earns its place by writing the line
        a person would write, from the same data, rather than by occupying a
        corner of the screen waiting to be asked something.
        """
        if not bands:
            return _("Rien ne réclame votre attention ce matin.")
        worst = next((b for b in bands if b["sev"] == CRIT), bands[0])
        first = worst["rows"][0]
        rest = sum(b["count"] for b in bands) - 1
        if rest <= 0:
            return _("Une seule chose ce matin : %s.") % first["title"].lower()
        return _("Le plus pressant : %s. %s autres éléments attendent.") % (
            first["title"].lower(), rest)
