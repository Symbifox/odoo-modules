from odoo import api, fields, models


class SmsDashboard(models.Model):
    _name = "sms.archive.dashboard"
    _description = "Tableau de bord SMS & Appels"
    _auto = False

    @api.model
    def get_dashboard_data(self, date_from=False, date_to=False):
        """Statistiques du propriétaire uniquement.

        Volontairement aveugle aux lignes partagées : le trafic d'un numéro
        appartient à son propriétaire, et un lecteur partagé n'a pas à en
        déduire des volumes sur des messages qu'il ne peut pas lire."""
        uid = self.env.uid

        return {
            "kpis": self._get_kpis(uid, date_from, date_to),
            "monthly_sms": self._get_monthly_sms(uid, date_from, date_to),
            "monthly_calls": self._get_monthly_calls(uid, date_from, date_to),
            "top_contacts": self._get_top_contacts(uid, date_from, date_to),
            "call_stats": self._get_call_stats(uid, date_from, date_to),
        }

    @staticmethod
    def _date_clause(alias, date_field, date_from, date_to):
        """Return (sql_fragment, params) for optional date filtering."""
        clauses, params = [], []
        if date_from:
            clauses.append(f"{alias}.{date_field} >= %s")
            params.append(date_from)
        if date_to:
            clauses.append(f"{alias}.{date_field} <= %s")
            params.append(date_to + " 23:59:59" if len(date_to) == 10 else date_to)
        return (" AND ".join(clauses), params)

    def _get_kpis(self, uid, date_from, date_to):
        # Message counts
        msg_clause, msg_params = self._date_clause("m", "date_sent", date_from, date_to)
        msg_where = f"AND {msg_clause}" if msg_clause else ""
        self.env.cr.execute(f"""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE m.direction = 'in') AS received,
                COUNT(*) FILTER (WHERE m.direction = 'out') AS sent,
                COUNT(*) FILTER (WHERE m.is_mms = TRUE) AS mms
            FROM sms_archive_message m
            JOIN sms_archive_thread t ON m.thread_id = t.id
            WHERE t.owner_id = %s {msg_where}
        """, [uid] + msg_params)
        msg = self.env.cr.dictfetchone()

        # Call counts
        call_clause, call_params = self._date_clause("c", "date", date_from, date_to)
        call_where = f"AND {call_clause}" if call_clause else ""
        self.env.cr.execute(f"""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE c.call_type = 'incoming') AS incoming,
                COUNT(*) FILTER (WHERE c.call_type = 'outgoing') AS outgoing,
                COUNT(*) FILTER (WHERE c.call_type = 'missed') AS missed
            FROM call_archive_call c
            JOIN sms_archive_thread t ON c.thread_id = t.id
            WHERE t.owner_id = %s {call_where}
        """, [uid] + call_params)
        calls = self.env.cr.dictfetchone()

        # Thread counts (not date-filtered — threads are permanent)
        self.env.cr.execute("""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE partner_id IS NOT NULL) AS matched,
                COUNT(*) FILTER (WHERE partner_id IS NULL) AS unmatched
            FROM sms_archive_thread
            WHERE owner_id = %s AND active = TRUE
        """, (uid,))
        threads = self.env.cr.dictfetchone()

        return {
            "messages": msg,
            "calls": calls,
            "threads": threads,
        }

    def _get_monthly_sms(self, uid, date_from, date_to):
        clause, params = self._date_clause("m", "date_sent", date_from, date_to)
        date_where = f"AND {clause}" if clause else ""
        self.env.cr.execute(f"""
            SELECT
                TO_CHAR(m.date_sent, 'YYYY-MM') AS month,
                COUNT(*) FILTER (WHERE m.direction = 'in') AS received,
                COUNT(*) FILTER (WHERE m.direction = 'out') AS sent
            FROM sms_archive_message m
            JOIN sms_archive_thread t ON m.thread_id = t.id
            WHERE t.owner_id = %s {date_where}
            GROUP BY TO_CHAR(m.date_sent, 'YYYY-MM')
            ORDER BY month ASC
        """, [uid] + params)
        return self.env.cr.dictfetchall()

    def _get_monthly_calls(self, uid, date_from, date_to):
        clause, params = self._date_clause("c", "date", date_from, date_to)
        date_where = f"AND {clause}" if clause else ""
        self.env.cr.execute(f"""
            SELECT
                TO_CHAR(c.date, 'YYYY-MM') AS month,
                COUNT(*) FILTER (WHERE c.call_type = 'incoming') AS incoming,
                COUNT(*) FILTER (WHERE c.call_type = 'outgoing') AS outgoing,
                COUNT(*) FILTER (WHERE c.call_type = 'missed') AS missed
            FROM call_archive_call c
            JOIN sms_archive_thread t ON c.thread_id = t.id
            WHERE t.owner_id = %s {date_where}
            GROUP BY TO_CHAR(c.date, 'YYYY-MM')
            ORDER BY month ASC
        """, [uid] + params)
        return self.env.cr.dictfetchall()

    def _get_top_contacts(self, uid, date_from, date_to):
        # When filtered by date, count interactions in that period
        msg_clause, msg_params = self._date_clause("m", "date_sent", date_from, date_to)
        msg_where = f"AND {msg_clause}" if msg_clause else ""
        call_clause, call_params = self._date_clause("ca", "date", date_from, date_to)
        call_where = f"AND {call_clause}" if call_clause else ""

        self.env.cr.execute(f"""
            SELECT
                t.id,
                COALESCE(t.contact_name, t.phone_normalized) AS name,
                t.phone_normalized AS phone,
                t.partner_id,
                COALESCE(p.name, '') AS partner_name,
                COALESCE(mc.msg_count, 0) AS message_count,
                COALESCE(cc.call_count, 0) AS call_count,
                COALESCE(mc.msg_count, 0) + COALESCE(cc.call_count, 0) AS total_interactions
            FROM sms_archive_thread t
            LEFT JOIN res_partner p ON t.partner_id = p.id
            LEFT JOIN (
                SELECT m.thread_id, COUNT(*) AS msg_count
                FROM sms_archive_message m
                WHERE 1=1 {msg_where}
                GROUP BY m.thread_id
            ) mc ON mc.thread_id = t.id
            LEFT JOIN (
                SELECT ca.thread_id, COUNT(*) AS call_count
                FROM call_archive_call ca
                WHERE 1=1 {call_where}
                GROUP BY ca.thread_id
            ) cc ON cc.thread_id = t.id
            WHERE t.owner_id = %s
              AND t.active = TRUE
              AND t.is_hidden = FALSE
              AND (COALESCE(mc.msg_count, 0) + COALESCE(cc.call_count, 0)) > 0
            ORDER BY total_interactions DESC
            LIMIT 10
        """, msg_params + call_params + [uid])
        return self.env.cr.dictfetchall()

    def _get_call_stats(self, uid, date_from, date_to):
        clause, params = self._date_clause("c", "date", date_from, date_to)
        date_where = f"AND {clause}" if clause else ""
        self.env.cr.execute(f"""
            SELECT
                COALESCE(AVG(c.duration) FILTER (
                    WHERE c.call_type IN ('incoming', 'outgoing') AND c.duration > 0
                ), 0) AS avg_duration,
                COALESCE(SUM(c.duration), 0) AS total_duration,
                COUNT(*) FILTER (WHERE c.call_type = 'missed') AS missed_count,
                COUNT(*) AS total_count,
                COALESCE(MAX(c.duration), 0) AS longest_call
            FROM call_archive_call c
            JOIN sms_archive_thread t ON c.thread_id = t.id
            WHERE t.owner_id = %s {date_where}
        """, [uid] + params)
        row = self.env.cr.dictfetchone()
        total = row["total_count"] or 0
        missed = row["missed_count"] or 0
        return {
            "avg_duration": round(float(row["avg_duration"])),
            "total_duration": int(row["total_duration"]),
            "missed_count": missed,
            "total_count": total,
            "missed_rate": round(missed * 100 / total) if total else 0,
            "longest_call": int(row["longest_call"]),
        }

    @api.model
    def action_open_model(self, model, name, view_type="list", domain=None):
        views_map = {
            "list": [(False, "list"), (False, "form")],
        }
        action = {
            "type": "ir.actions.act_window",
            "name": name,
            "res_model": model,
            "views": views_map.get(view_type, [(False, "list"), (False, "form")]),
            "target": "current",
        }
        if domain:
            action["domain"] = domain
        return action
