from collections import Counter, defaultdict

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class SurveyQuestion(models.Model):
    _inherit = "survey.question"

    bf_one_per_column = fields.Boolean(
        string="One row per column",
        help="The respondent may tick at most one row under each column "
        "(for example one need as priority 1, one as priority 2). Checked "
        "before the page is sent, then by the server. In a matrix with "
        "several answers per row, one row may still be ticked under several "
        "columns: use one answer per row for a ranking.",
    )
    bf_rank_weighted = fields.Boolean(
        string="Weighted trend",
        compute="_compute_bf_rank_weighted",
        help="A matrix whose columns carry points: its weighted trend is shown "
        "on the results page and can be exported.",
    )

    @api.depends("question_type", "suggested_answer_ids.bf_rank_points")
    def _compute_bf_rank_weighted(self):
        for question in self:
            question.bf_rank_weighted = question.question_type == "matrix" and any(
                column.bf_rank_points > 0 for column in question.suggested_answer_ids
            )

    # ------------------------------------------------------------------
    # One row per column
    # ------------------------------------------------------------------
    @api.constrains(
        "bf_one_per_column",
        "constr_mandatory",
        "question_type",
        "matrix_row_ids",
        "suggested_answer_ids",
    )
    def _check_bf_one_per_column(self):
        # A mandatory matrix wants an answer on every row; with at most one
        # row per column, more rows than columns can never be filled in.
        for question in self:
            if (
                question.question_type == "matrix"
                and question.bf_one_per_column
                and question.constr_mandatory
                and len(question.matrix_row_ids) > len(question.suggested_answer_ids)
            ):
                raise ValidationError(
                    self.env._(
                        "%(question)s: a mandatory matrix needs an answer on every "
                        'row, and "One row per column" allows only one row under '
                        "each column. With more rows than columns, nobody could "
                        "finish the survey: make the question optional or uncheck "
                        '"One row per column".',
                        question=question.title or "",
                    )
                )

    def _bf_one_per_column_message(self):
        return self.env._("Choose only one row in each column: uncheck the extra rows.")

    def _validate_matrix(self, answers):
        errors = super()._validate_matrix(answers)
        if errors or not self.bf_one_per_column:
            return errors
        # answers = {row_id: [column_id, ...]}, already checked by the native
        # code: known rows, known columns, no column twice on the same row.
        # Counted as the native code saves them, as integers: "7" and "07"
        # are the same column.
        per_column = Counter(
            int(column) for row_answers in answers.values() for column in row_answers
        )
        if any(count > 1 for count in per_column.values()):
            return {self.id: self._bf_one_per_column_message()}
        return {}

    # ------------------------------------------------------------------
    # Weighted trend
    # ------------------------------------------------------------------
    def _bf_rank_trend(self, user_input_lines):
        """The weighted trend of this matrix over the given answer lines.

        Each respondent's ticks are read column by column: a column's points
        go to the row ticked under it, and are shared equally when several
        rows were ticked under the same column. A respondent thus never
        weighs more than the column points added up, however many boxes they
        ticked. A column without points still counts its mentions.
        """
        self.ensure_one()
        columns = self.suggested_answer_ids
        rows = self.matrix_row_ids
        points = {column: column.bf_rank_points for column in columns}

        # respondent -> column -> rows ticked
        ticks = defaultdict(lambda: defaultdict(set))
        for line in user_input_lines:
            if (
                line.question_id == self
                and not line.skipped
                and line.matrix_row_id in rows
                and line.suggested_answer_id in columns
            ):
                ticks[line.user_input_id][line.suggested_answer_id].add(
                    line.matrix_row_id
                )

        score = dict.fromkeys(rows, 0.0)
        mentions = {row: Counter() for row in rows}
        shared = 0
        for by_column in ticks.values():
            if any(len(ticked) > 1 for ticked in by_column.values()):
                shared += 1
            for column, ticked in by_column.items():
                part = points[column] / len(ticked)
                for row in ticked:
                    score[row] += part
                    mentions[row][column] += 1

        total = sum(score.values())
        top = max(score.values(), default=0.0)

        def share(value):
            return round(100.0 * value / total, 1) if total else 0.0

        def bar(value):
            return round(100.0 * value / top, 1) if top else 0.0

        ordered = sorted(
            rows, key=lambda row: (-round(score[row], 6), row.sequence, row.id)
        )
        out_rows = []
        for row in ordered:
            out_rows.append(
                {
                    "row": row,
                    "label": row.value or "",
                    "group": (row.bf_rank_group or "").strip(),
                    "points": round(score[row], 2),
                    "share": share(score[row]),
                    "bar": bar(score[row]),
                    "mentions": [mentions[row][column] for column in columns],
                }
            )
        self._bf_rank_number(out_rows)

        groups = []
        if any(r["group"] for r in out_rows):
            # "Money" and "money" are one group, shown as written on its first
            # row in the matrix (a label that does not move with the answers).
            # Rows without a group are keyed apart: a group typed "No group"
            # stays its own.
            labels = {}
            for row in rows:
                group = (row.bf_rank_group or "").strip()
                if group:
                    labels.setdefault(group.casefold(), group)
            by_group = {}
            for r in out_rows:
                key = r["group"].casefold() if r["group"] else None
                g = by_group.setdefault(
                    key,
                    {
                        "label": labels[key] if key else self.env._("No group"),
                        "points": 0.0,
                        "rows": 0,
                    },
                )
                g["points"] += score[r["row"]]
                g["rows"] += 1
            group_top = max(g["points"] for g in by_group.values())
            for _key, g in sorted(
                by_group.items(),
                key=lambda kv: (-round(kv[1]["points"], 6), kv[0] is None, kv[0] or ""),
            ):
                label = g["label"]
                groups.append(
                    {
                        "label": label,
                        "points": round(g["points"], 2),
                        "share": share(g["points"]),
                        "bar": round(100.0 * g["points"] / group_top, 1)
                        if group_top
                        else 0.0,
                        "rows": g["rows"],
                    }
                )
            self._bf_rank_number(groups)

        return {
            "columns": [
                {"label": column.value or "", "points": column.bf_rank_points}
                for column in columns
            ],
            "respondents": len(ticks),
            "shared": shared,
            "total": round(total, 2),
            "rows": out_rows,
            "groups": groups,
        }

    @staticmethod
    def _bf_rank_number(items):
        """Rank 1, 2, 2, 4: equal points share a rank; no points, no rank."""
        previous, rank = None, 0
        for index, item in enumerate(items, start=1):
            if item["points"] != previous:
                rank, previous = index, item["points"]
            item["rank"] = rank if item["points"] > 0 else None

    def _bf_rank_mentions(self, item, row):
        """ "Priority 1: 3 · Priority 2: 1 · …", one row's mentions in words."""
        return " · ".join(
            self.env._("%(column)s: %(count)s", column=column["label"], count=count)
            for column, count in zip(item["columns"], row["mentions"], strict=True)
        )

    def _bf_rank_rule(self):
        """ "Priority 1 = 10 pts · Priority 2 = 5 pts · …", the scale in words."""
        self.ensure_one()
        return " · ".join(
            self.env._(
                "%(column)s = %(points)s pts",
                column=column.value or "",
                points=self.survey_id._bf_num(column.bf_rank_points),
            )
            for column in self.suggested_answer_ids
        )

    def _prepare_statistics(self, user_input_lines):
        all_data = super()._prepare_statistics(user_input_lines)
        for data in all_data:
            question = data["question"]
            if not data.get("is_page") and question.bf_rank_weighted:
                data["bf_rank"] = question._bf_rank_trend(data["answer_line_done_ids"])
        return all_data
