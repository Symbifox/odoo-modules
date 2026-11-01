import json
import math
import re
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

POS_USER = "point_of_sale.group_pos_user"
STATES = [("seated", "Seated"), ("ordered", "Ordered"), ("to_clear", "To clear"), ("done", "Cleared"),
          ("left", "Left")]
LIVE = ("seated", "ordered", "to_clear")
# Meals of this register used for the usual length, and how many of a party size make it trusted.
TURN_HISTORY = timedelta(days=60)
TURN_SAMPLES = 3
PACE_WINDOW = timedelta(minutes=15)
FORGOTTEN_TICKET = timedelta(hours=4)
CLEAR_MINUTES = 5
# The rotation counts what a section's server took in the last hours of the service, not since the session opened.
SERVICE_WINDOW = timedelta(hours=4)
MAX_PARTY = 99


def _bucket(party):
    """Party sizes whose meals last about as long: 1-2, 3-4, 5-6, 7 and more."""
    return min((max(int(party or 1), 1) + 1) // 2, 4)


def _minutes(delta):
    return max(int(delta.total_seconds() // 60), 0)


def _root(table):
    """The head of a group of joined tables: Odoo joins a table to a table already joined, so climb to the top."""
    seen = set()
    while table.parent_id and table.id not in seen:
        seen.add(table.id)
        table = table.parent_id
    return table


def without_notes(text, notes):
    """`text` without the host's notes, each as whole words (an allergy « Vin » leaves « Vinaigrette » alone), the
    separators left behind tidied. None when no note was in it."""
    if not text:
        return None
    out = text
    for note in sorted({n.strip() for n in notes if n and n.strip()}, key=len, reverse=True):
        out = re.sub(r"(?<!\w)%s(?!\w)" % re.escape(note), "", out)
    if out == text:
        return None
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\s*([,;])(\s*[,;])+", r"\1", out)
    return re.sub(r"^[\s,;.:\-]+|[\s,;:\-]+$", "", out)


class HostSeating(models.Model):
    """A party seated at a table, from the host's seating to the table cleared. Odoo keeps no trace of a
    table opened without an order: this record is the seating, and its times make the length of a meal."""
    _name = "bf.host.seating"
    _description = "Seating"
    _order = "seated_at desc, id desc"

    company_id = fields.Many2one(related="config_id.company_id", store=True, index=True)
    config_id = fields.Many2one("pos.config", "Register", required=True, readonly=True, index=True,
                                ondelete="cascade")
    session_id = fields.Many2one("pos.session", readonly=True, index=True, ondelete="set null")
    table_id = fields.Many2one("restaurant.table", readonly=True, index=True, ondelete="set null")
    table_name = fields.Char("Table name", readonly=True)
    section_id = fields.Many2one("bf.host.section", readonly=True, ondelete="set null")
    employee_id = fields.Many2one("hr.employee", "Server", readonly=True, ondelete="set null")
    host_id = fields.Many2one("hr.employee", "Seated by", readonly=True, ondelete="set null")
    party_size = fields.Integer("Covers", readonly=True, default=1)
    guest_name = fields.Char("Name", readonly=True, help="Erased when the session closes.")
    note = fields.Char(readonly=True, help="Passed to the order. Erased when the session closes, from the order and "
                                           "its kitchen tickets too.")
    note_history = fields.Text(readonly=True, groups="base.group_system",
                               help="Every note the seating had, to erase them all from the orders. Erased with it.")
    state = fields.Selection(STATES, required=True, default="seated", readonly=True, index=True)
    seated_at = fields.Datetime(required=True, readonly=True, default=fields.Datetime.now)
    ordered_at = fields.Datetime(readonly=True)
    paid_at = fields.Datetime(readonly=True)
    cleared_at = fields.Datetime(readonly=True)
    duration = fields.Integer("Meal (minutes)", compute="_compute_duration", store=True, aggregator="avg",
                              help="From seated to paid.")
    order_ids = fields.One2many("pos.order", "bf_host_seating_id", string="Orders", readonly=True)
    wait_id = fields.Many2one("bf.host.wait", "From the waiting list", readonly=True, ondelete="set null")

    @api.depends("seated_at", "paid_at")
    def _compute_duration(self):
        for seating in self:
            seating.duration = (_minutes(seating.paid_at - seating.seated_at)
                                if seating.paid_at and seating.seated_at else 0)

    @api.model
    def _bf_party(self, party_size):
        try:
            party = int(party_size or 0)
        except (TypeError, ValueError):
            party = 0
        if not 1 <= party <= MAX_PARTY:
            raise UserError(_("A party has from 1 to %s guests.", MAX_PARTY))
        return party

    @api.depends("table_name", "party_size")
    def _compute_display_name(self):
        for seating in self:
            seating.display_name = _("%(table)s, %(covers)s covers", table=seating.table_name or "?",
                                     covers=seating.party_size)

    # --- access: the register's users, on a register of their companies with the host stand on
    @api.model
    def _bf_config(self, config_id):
        if not self.env.user.has_group(POS_USER):
            raise AccessError(_("The host stand is for the register's users."))
        config = self.env["pos.config"].browse(int(config_id)).exists()
        if not config:
            raise UserError(_("This register no longer exists."))
        config.check_access("read")
        if not config.bf_host:
            raise UserError(_("The host stand is not on at this register."))
        return config.sudo()

    def _bf_own(self):
        """A seating of a register the user may use (the methods then write as sudo)."""
        self.ensure_one()
        self._bf_config(self.sudo().config_id.id)
        return self.sudo()

    # --- the floor
    @api.model
    @api.model
    def _bf_tables(self, config):
        return config.floor_ids.filtered("active").table_ids.filtered("active")

    @api.model
    def _bf_group(self, table):
        """A table and the tables joined to it (Odoo joins tables by dragging one onto another)."""
        group = _root(table)
        Table = self.env["restaurant.table"].sudo()
        children = Table.search([("parent_id", "in", group.ids), ("active", "=", True)])
        while children - group:
            group |= children
            children = Table.search([("parent_id", "in", group.ids), ("active", "=", True)])
        return group

    @api.model
    def _bf_capacity(self, tables):
        """{head table id: seats}, the seats of the tables joined to it included."""
        capacity = {}
        for table in tables:
            head = _root(table)
            capacity[head.id] = capacity.get(head.id, 0) + table.seats
        return capacity

    @api.model
    def _bf_live(self, config):
        return self.sudo().search([("config_id", "=", config.id), ("state", "in", LIVE)])

    @api.model
    def _bf_open_orders(self, config, tables):
        return self.env["pos.order"].sudo().search([
            ("config_id", "=", config.id), ("state", "=", "draft"), ("table_id", "in", tables.ids)])

    @api.model
    def _bf_section_of(self, config, table):
        table = _root(table)
        return config.bf_host_section_ids.filtered(lambda s: s.active and table in s.table_ids)[:1]

    @api.model
    def _bf_table_states(self, config, tables=None):
        """{table id: (state, seating)} for the tables of the register: free, seated, ordered, served
        (every kitchen ticket served), to_clear. Joined tables share the state of their head table, whichever
        of them the party was seated at."""
        tables = self._bf_tables(config) if tables is None else tables
        tables = tables | self.env["restaurant.table"].browse([_root(t).id for t in tables])
        # The newest seating of a group of tables wins (the list is newest first).
        live = {}
        for seating in reversed(self._bf_live(config)):
            if seating.table_id:
                live[_root(seating.table_id).id] = seating
        orders = self._bf_open_orders(config, self._bf_tables(config))
        with_lines = {_root(o.table_id).id for o in orders if o.lines}
        tickets = self.env["bf.kitchen.ticket"].sudo().search(
            [("order_id", "in", list({o.id for s in live.values() for o in s.order_ids}))])
        tickets_by_order = {}
        for ticket in tickets:
            tickets_by_order.setdefault(ticket.order_id.id, []).append(ticket.state)
        states = {}
        for table in tables:
            head = _root(table)
            seating = live.get(head.id)
            if seating:
                state = seating.state
                if state == "ordered":
                    done = [st for o in seating.order_ids.filtered(lambda o: o.state == "draft")
                            for st in tickets_by_order.get(o.id, [])]
                    if done and all(st in ("served", "cancelled") for st in done) and "served" in done:
                        state = "served"
            elif head.id in with_lines:
                state = "ordered"
            else:
                state = "free"
            states[table.id] = (state, seating or self.browse())
        return states

    @api.model
    def _bf_service_start(self, config, now):
        session = config.current_session_id
        start = (session.start_at or session.create_date) if session else now - SERVICE_WINDOW
        return max(start, now - SERVICE_WINDOW)

    @api.model
    def _bf_sections(self, config, now):
        """Each section: covers its server took in the service, tables open, minutes since its server's last
        seating. A server who takes over a section at the evening service does not inherit the noon."""
        seatings = self.sudo().search([("config_id", "=", config.id), ("state", "!=", "left"), "|",
                                       ("seated_at", ">=", self._bf_service_start(config, now)),
                                       ("state", "in", LIVE)])
        result = []
        for section in config.bf_host_section_ids.filtered("active"):
            in_section = seatings.filtered(lambda s: s.section_id == section)
            mine = in_section.filtered(lambda s: s.employee_id == section.employee_id
                                       and s.seated_at >= self._bf_service_start(config, now))
            last = max(mine.mapped("seated_at")) if mine else False
            result.append({
                "section": section,
                "covers": sum(mine.mapped("party_size")),
                "open": len(in_section.filtered(lambda s: s.state in LIVE)),
                "last_minutes": _minutes(now - last) if last else None,
            })
        return result

    @api.model
    def _bf_suggest(self, config, party, table=None, states=None, now=None):
        """The table to seat a party at: the free tables of the section next in turn (fewest covers this
        service, then the longest without a seating), the smallest that fits. With `table`, the warnings
        for seating there instead: not this section's turn, double seating, too small, much too large."""
        now = now or fields.Datetime.now()
        states = self._bf_table_states(config) if states is None else states
        all_tables = self._bf_tables(config)
        capacity = self._bf_capacity(all_tables)
        free = all_tables.filtered(lambda t: not t.parent_id and states[t.id][0] == "free")
        delay = config.bf_host_double_seat_minutes
        sections = [s for s in self._bf_sections(config, now) if s["section"].employee_id]

        def recent(info):
            return delay and info["last_minutes"] is not None and info["last_minutes"] < delay

        def fits(tables):
            return tables.filtered(lambda t: capacity.get(t.id, t.seats) >= party)

        ranked = sorted(
            [s for s in sections if free & s["section"].table_ids],
            key=lambda s: (not fits(free & s["section"].table_ids), bool(recent(s)), s["covers"],
                           -(s["last_minutes"] if s["last_minutes"] is not None else 10 ** 6), s["section"].sequence))
        best = ranked[0] if ranked else None
        if best:
            pool = fits(free & best["section"].table_ids) or (free & best["section"].table_ids)
        else:
            pool = fits(free) or free
        suggested = pool.sorted(lambda t: (capacity.get(t.id, t.seats) < party, capacity.get(t.id, t.seats),
                                           t.table_number))[:1]
        warnings = []
        target = _root(table) if table else suggested
        if target:
            seats = capacity.get(target.id, target.seats)
            info = next((s for s in sections if target in s["section"].table_ids), None)
            if table and best and info and info["section"] != best["section"]:
                warnings.append(_("It is section %(next)s's turn (%(server)s), not %(this)s.",
                                  next=best["section"].name, server=best["section"].employee_id.name,
                                  this=info["section"].name))
            if info and recent(info):
                warnings.append(_("Double seating: section %(section)s was seated %(minutes)s min ago.",
                                  section=info["section"].name, minutes=info["last_minutes"]))
            if seats < party:
                warnings.append(_("Table %(table)s seats %(seats)s, for a party of %(party)s.",
                                  table=target.table_number, seats=seats, party=party))
            elif seats >= party + 3 and fits(free).filtered(lambda t: capacity.get(t.id, t.seats) < seats):
                warnings.append(_("Table %(table)s seats %(seats)s: a smaller table is free.",
                                  table=target.table_number, seats=seats))
        return {"table": suggested, "section": best["section"] if best else False, "warnings": warnings}

    # --- quoting a wait
    @api.model
    def _bf_turns(self, config, now):
        """{party size bucket: usual meal length}: the meals of this register in the last 60 days, one query;
        the register's setting for a bucket with fewer than 3 meals."""
        groups = self.sudo()._read_group(
            [("config_id", "=", config.id), ("paid_at", "!=", False), ("seated_at", ">=", now - TURN_HISTORY),
             ("duration", ">", 0)],
            ["party_size"], ["duration:sum", "__count"])
        sums = {}
        for party_size, total, count in groups:
            bucket = _bucket(party_size)
            done, meals = sums.get(bucket, (0, 0))
            sums[bucket] = (done + total, meals + count)
        turns = {}
        for bucket in (1, 2, 3, 4):
            total, count = sums.get(bucket, (0, 0))
            turns[bucket] = (max(int(round(total / count)), CLEAR_MINUTES) if count >= TURN_SAMPLES
                             else config.bf_host_turn_minutes)
        return turns

    @api.model
    def _bf_quote(self, config, party, ahead, states, now, turns=None):
        """Minutes to quote a party, with `ahead` parties waiting before it: when the tables that fit it free
        up, from the usual length of its meals; rounded up to 5 minutes, then 5 more (quote long, beat it)."""
        all_tables = self._bf_tables(config)
        capacity = self._bf_capacity(all_tables)
        tables = all_tables.filtered(lambda t: not t.parent_id)
        fitting = tables.filtered(lambda t: capacity.get(t.id, t.seats) >= party) or tables
        if not fitting:
            return 0
        turn = (turns or self._bf_turns(config, now))[_bucket(party)]
        remaining = []
        for table in fitting:
            state, seating = states[table.id]
            if state == "free":
                remaining.append(0)
            elif state == "to_clear":
                remaining.append(CLEAR_MINUTES)
            else:
                elapsed = _minutes(now - seating.seated_at) if seating else 0
                remaining.append(max(turn - elapsed, CLEAR_MINUTES))
        remaining.sort()
        rounds, index = divmod(ahead, len(remaining))
        minutes = remaining[index] + rounds * turn
        if not minutes:
            return 0
        return int(math.ceil(minutes / 5.0) * 5) + 5

    # --- the pace
    @api.model
    def _bf_pace(self, config, now):
        recent = self.sudo().search([("config_id", "=", config.id), ("seated_at", ">=", now - PACE_WINDOW),
                                     ("state", "!=", "left")])
        covers = sum(recent.mapped("party_size"))
        # The kitchen's backlog now: this session's tickets not started or in progress. A ticket left open for
        # hours was forgotten (served without a press), not a backlog.
        tickets = self.env["bf.kitchen.ticket"].sudo().search([
            ("config_id", "=", config.id), ("state", "in", ("new", "progress")),
            ("session_id", "=", config.current_session_id.id), ("sent_at", ">=", now - FORGOTTEN_TICKET)])
        oldest = max((_minutes(now - t.sent_at) for t in tickets), default=0)
        limit, alert = config.bf_host_pace_covers, config.bf_host_ticket_alert_minutes
        if (limit and covers >= limit) or (alert and tickets and oldest >= alert):
            level = "red"
        elif (limit and covers >= 0.75 * limit) or (alert and tickets and oldest >= 0.75 * alert):
            level = "amber"
        else:
            level = "green"
        return {"covers": covers, "limit": limit, "tickets": len(tickets), "oldest": oldest, "level": level}

    # --- what the register shows
    @api.model
    def bf_host_data(self, config_id):
        config = self._bf_config(config_id)
        now = fields.Datetime.now()
        states = self._bf_table_states(config)
        turns = self._bf_turns(config, now)
        tables = {}
        for table_id, (state, seating) in states.items():
            row = {"state": state}
            if seating:
                row.update({
                    "seating_id": seating.id, "covers": seating.party_size, "name": seating.guest_name or "",
                    "note": seating.note or "", "server": seating.employee_id.name or "",
                    "minutes": _minutes(now - seating.seated_at), "has_order": bool(seating.order_ids),
                })
            section = self._bf_section_of(config, self.env["restaurant.table"].browse(table_id))
            if section:
                row.update({"section_id": section.id, "section": section.name, "color": section.color})
            tables[table_id] = row
        waits = self.env["bf.host.wait"].sudo().search([("config_id", "=", config.id), ("state", "=", "waiting")],
                                                        order="arrived_at, id")
        wait_rows = []
        for ahead, wait in enumerate(waits):
            wait_rows.append({
                "id": wait.id, "name": wait.guest_name or "", "covers": wait.party_size, "note": wait.note or "",
                "minutes": _minutes(now - wait.arrived_at), "quoted": wait.quoted_minutes,
                "estimate": self._bf_quote(config, wait.party_size, ahead, states, now, turns),
            })
        sections = self._bf_sections(config, now)
        suggestion = self._bf_suggest(config, 2, states=states, now=now)
        return {
            "tables": tables,
            "sections": [{
                "id": s["section"].id, "name": s["section"].name, "color": s["section"].color,
                "employee_id": s["section"].employee_id.id, "server": s["section"].employee_id.name or "",
                "covers": s["covers"], "open": s["open"], "last_minutes": s["last_minutes"],
            } for s in sections],
            "next": {"section_id": suggestion["section"].id if suggestion["section"] else False,
                     "table_id": suggestion["table"].id},
            "waits": wait_rows,
            "quote": {str(p): self._bf_quote(config, p, len(waits), states, now, turns) for p in (2, 4, 6)},
            "pace": self._bf_pace(config, now),
            "servers": [{"id": e.id, "name": e.name} for e in self._bf_servers(config)],
        }

    @api.model
    def _bf_servers(self, config):
        employees = self.env["hr.employee"].sudo().search(config._employee_domain(config.current_user_id.id))
        return employees - config.bf_host_employee_ids

    @api.model
    def bf_host_suggest(self, config_id, party_size, table_id=False):
        config = self._bf_config(config_id)
        table = self.env["restaurant.table"].browse(int(table_id)) if table_id else None
        if table and table not in self._bf_tables(config):
            raise UserError(_("This table is not on this register's floors."))
        result = self._bf_suggest(config, self._bf_party(party_size), table=table)
        section = self._bf_section_of(config, table or result["table"]) if (table or result["table"]) else False
        return {"table_id": result["table"].id, "table": result["table"].table_number,
                "server": section.employee_id.name if section else "", "warnings": result["warnings"]}

    # --- what the host does
    @api.model
    def _bf_free_group(self, config, table):
        """The head of a free group of tables: two devices cannot seat the same table at once."""
        if not table or table not in self._bf_tables(config):
            raise UserError(_("This table is not on this register's floors."))
        group = self._bf_group(table)
        # A write, not a FOR UPDATE: under REPEATABLE READ a second device would keep its snapshot from before
        # the first seating; the write makes it fail to serialise, and Odoo replays it, seeing the seating.
        self.env.cr.execute("UPDATE restaurant_table SET write_date = write_date WHERE id IN %s", [tuple(group.ids)])
        head = _root(table)
        if self._bf_table_states(config, head)[head.id][0] != "free":
            raise UserError(_("Table %s is not free.", head.table_number))
        return head

    @api.model
    def bf_host_seat(self, config_id, table_id, party_size, guest_name="", note="", host_id=False, wait_id=False):
        config = self._bf_config(config_id)
        party = self._bf_party(party_size)
        table = self._bf_free_group(config, self.env["restaurant.table"].sudo().browse(int(table_id)).exists())
        section = self._bf_section_of(config, table)
        host = self.env["hr.employee"].sudo().browse(int(host_id)).exists() if host_id else False
        if host and host.company_id not in config.company_id:
            host = False
        wait = self.env["bf.host.wait"].sudo().browse(int(wait_id)).exists() if wait_id else False
        if wait and (wait.config_id != config or wait.state != "waiting"):
            raise UserError(_("This party is no longer waiting."))
        seating = self.sudo().create({
            "config_id": config.id, "session_id": config.current_session_id.id, "table_id": table.id,
            "table_name": table.display_name, "section_id": section.id, "employee_id": section.employee_id.id,
            "host_id": host.id if host else False, "party_size": party,
            "guest_name": (guest_name or "").strip()[:80] or False, "note": (note or "").strip()[:200] or False,
            "note_history": (note or "").strip()[:200] or False, "wait_id": wait.id if wait else False,
        })
        if wait:
            wait.write({"state": "seated", "seated_at": seating.seated_at, "seating_id": seating.id})
        return seating.id

    def bf_host_update(self, party_size, guest_name="", note=""):
        seating = self._bf_own()
        if seating.state not in LIVE:
            raise UserError(_("This party has left."))
        note = (note or "").strip()[:200] or False
        history = [n for n in (seating.note_history or "").split("\n") if n]
        if note and note not in history:
            history.append(note)
        seating.write({"party_size": self._bf_party(party_size),
                       "guest_name": (guest_name or "").strip()[:80] or False,
                       "note": note, "note_history": "\n".join(history) or False})
        return True

    def bf_host_move(self, table_id):
        """A seated party moved before ordering. Once it ordered, the server moves the order (Odoo's transfer)."""
        seating = self._bf_own()
        config = seating.config_id
        if seating.state != "seated" or seating.order_ids:
            raise UserError(_("This party has ordered: move its order from the table."))
        table = self._bf_free_group(config, self.env["restaurant.table"].sudo().browse(int(table_id)).exists())
        section = self._bf_section_of(config, table)
        vals = {"table_id": table.id, "table_name": table.display_name}
        if section != seating.section_id:
            vals.update({"section_id": section.id, "employee_id": section.employee_id.id})
        seating.write(vals)
        return True

    def bf_host_clear(self):
        """The table is cleared: after the payment (cleared), or a party seated that left without ordering."""
        seating = self._bf_own()
        if seating.state in ("done", "left"):
            raise UserError(_("This table is already cleared."))
        if seating.state == "to_clear":
            seating.write({"state": "done", "cleared_at": fields.Datetime.now()})
        elif seating.state == "seated" and not seating.order_ids.filtered(lambda o: o.state != "cancel"):
            seating.write({"state": "left", "cleared_at": fields.Datetime.now()})
        else:
            raise UserError(_("This table has an order still open."))
        return True

    @api.model
    def bf_host_assign(self, section_id, employee_id=False):
        section = self.env["bf.host.section"].sudo().browse(int(section_id)).exists()
        if not section:
            raise UserError(_("This section no longer exists."))
        config = self._bf_config(section.config_id.id)
        employee = self.env["hr.employee"].sudo().browse(int(employee_id)).exists() if employee_id else False
        if employee and employee not in self._bf_servers(config):
            raise UserError(_("%s does not work at this register.", employee.name))
        section.employee_id = employee.id if employee else False
        return True

    # --- following the register's orders
    @api.model
    def _bf_paid_at(self, orders, now):
        dates = [d for d in orders.payment_ids.mapped("payment_date") if d]
        return min(max(dates), now) if dates else now

    @api.model
    def _bf_follow_orders(self, orders):
        """Link each order of a table to its seating, and move the seating along: ordered, to clear once every
        order is paid, left if they were all cancelled. An order the register sends already paid (paid at
        once, or synchronised after being offline) is linked too; without a seating (the party was seated
        without the host), the first order makes one. Times come from the order and its payments when the
        register sent them late."""
        now = fields.Datetime.now()
        for order in orders.sudo():
            config = order.config_id
            if not config.bf_host:
                continue
            seating = order.bf_host_seating_id
            table = _root(order.table_id) if order.table_id else order.table_id
            if not seating and table and order.state != "cancel" and not order.lines:
                # Reserved without a dish yet (Odoo's Book button): linked to the party seated there, so that the
                # note copied to it is erased with the others.
                order.bf_host_seating_id = self.sudo().search([
                    ("config_id", "=", config.id), ("table_id", "in", self._bf_group(table).ids),
                    ("state", "=", "seated")], limit=1)
                continue
            if not seating and table and order.state != "cancel" and order.lines:
                group = self._bf_group(table)
                seating = self.sudo().search([("config_id", "=", config.id), ("table_id", "in", group.ids),
                                              ("state", "in", ("seated", "ordered"))], limit=1)
                if not seating:
                    # New guests at a table not cleared yet: the party before has gone.
                    self.sudo().search([("config_id", "=", config.id), ("table_id", "in", group.ids),
                                        ("state", "=", "to_clear")]).write({"state": "done", "cleared_at": now})
                    section = self._bf_section_of(config, table)
                    opened = min(order.date_order or now, now)
                    seating = self.sudo().create({
                        "config_id": config.id, "session_id": order.session_id.id, "table_id": table.id,
                        "table_name": table.display_name, "section_id": section.id,
                        "employee_id": (order.employee_id or section.employee_id).id,
                        "party_size": min(max(order.customer_count, 1), MAX_PARTY), "state": "ordered",
                        "seated_at": opened, "ordered_at": opened,
                    })
                order.bf_host_seating_id = seating
            if not seating:
                continue
            vals = {}
            live = seating.order_ids.filtered(lambda o: o.state != "cancel")
            if seating.state == "seated" and order.lines and order.state != "cancel":
                vals.update({"state": "ordered",
                             "ordered_at": max(seating.seated_at, min(order.date_order or now, now))})
            # The covers follow the party's first order (a split bill's part has its own count), but not a
            # register's default of 1 over the host's count.
            first = seating.order_ids.sorted("id")[:1]
            if (order == first and order.state != "cancel" and order.customer_count
                    and order.customer_count != seating.party_size
                    and (order.customer_count > 1 or not seating.host_id)):
                vals["party_size"] = min(order.customer_count, MAX_PARTY)
            seated_head = _root(seating.table_id) if seating.table_id else seating.table_id
            if (table and table != seated_head and order.state == "draft" and seating.state in LIVE
                    and live == order):
                # Moved onto a table where the host had seated a party that has not ordered: one party at a
                # table, the empty seating there is closed.
                self.sudo().search([("config_id", "=", config.id), ("id", "!=", seating.id),
                                    ("table_id", "in", self._bf_group(table).ids), ("state", "=", "seated")]) \
                    .filtered(lambda s: not s.order_ids.filtered(lambda o: o.state != "cancel")) \
                    .write({"state": "left", "cleared_at": now})
                vals.update({"table_id": table.id, "table_name": table.display_name})
            if seating.state in LIVE:
                if not live:
                    vals.update({"state": "left", "cleared_at": now})
                elif all(o.state in ("paid", "done", "invoiced") for o in live):
                    vals.update({"state": "to_clear", "paid_at": seating.paid_at or self._bf_paid_at(live, now)})
            if vals:
                seating.write(vals)

    # --- Loi 25
    def _bf_notes(self):
        """Every note the host gave these seatings, the current ones and the ones changed since."""
        notes = set()
        for seating in self.sudo():
            notes.update(n for n in (seating.note_history or "").split("\n") if n.strip())
            if seating.note:
                notes.add(seating.note)
        return notes

    @api.model
    def _bf_strip_notes(self, orders, notes):
        """Take the host's notes (an allergy) off orders, what they sent and their kitchen tickets, where the
        register copied them, and delete the receipts kept with the orders (they show the order's note)."""
        if not notes:
            return
        for order in orders.sudo():
            text = without_notes(order.general_note, notes)
            if text is not None:
                order.general_note = text or False
            raw = order.last_order_preparation_change
            try:
                data = json.loads(raw) if raw else None
            except ValueError:
                data = None
            if isinstance(data, dict):
                text = without_notes(data.get("generalNote"), notes)
                if text is not None:
                    data["generalNote"] = text
                    order.last_order_preparation_change = json.dumps(data)
            for ticket in self.env["bf.kitchen.ticket"].sudo().search([("order_id", "=", order.id)]):
                text = without_notes(ticket.general_note, notes)
                if text is not None:
                    ticket.general_note = text or False
        self.env["ir.attachment"].sudo().search([
            ("res_model", "=", "pos.order"), ("res_id", "in", orders.ids), ("name", "=like", "Receipt-%")]).unlink()

    @api.model
    def _bf_purge(self, domain):
        """Names and notes of guests are kept for the service only (Loi 25: personal information, an
        allergy is health information): erased from the seating, and the notes from the orders, the kitchen
        tickets and the receipts they were copied to. Besides the orders linked and their refunds, the orders of
        the same tables during the seating's time (an order not linked) are looked at too."""
        seatings = self.sudo().search(domain + ["|", "|", ("guest_name", "!=", False), ("note", "!=", False),
                                                ("note_history", "!=", False)])
        now = fields.Datetime.now()
        for seating in seatings:
            notes = seating._bf_notes()
            if not notes:
                continue
            tables = self._bf_group(seating.table_id).ids if seating.table_id else []
            nearby = self.env["pos.order"].sudo().search([
                ("config_id", "=", seating.config_id.id), ("general_note", "!=", False),
                "|", ("table_id", "in", tables), ("table_id", "=", False),
                ("date_order", ">=", seating.seated_at - timedelta(hours=1)),
                ("date_order", "<=", (seating.cleared_at or seating.paid_at or now) + timedelta(days=1))])
            self._bf_strip_notes(seating.order_ids | nearby | seating.order_ids.lines.refund_orderline_ids.order_id, notes)
        seatings.write({"guest_name": False, "note": False, "note_history": False})
        return seatings

    @api.model
    def _cron_bf_purge(self):
        """Every night: what a session never closed left behind, after a day."""
        limit = fields.Datetime.now() - timedelta(days=1)
        self._bf_purge([("seated_at", "<", limit)])
        waits = self.env["bf.host.wait"].sudo()
        waits.search([("arrived_at", "<", limit), ("state", "=", "waiting")]).write({"state": "left"})
        waits._bf_purge([("arrived_at", "<", limit)])
        stale = self.sudo().search([("seated_at", "<", limit), ("state", "=", "seated")])
        stale.write({"state": "left", "cleared_at": fields.Datetime.now()})
