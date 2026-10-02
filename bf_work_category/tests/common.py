from odoo.tests import TransactionCase


class WorkCategoryCase(TransactionCase):
    """Three category labels, one plain label, and a project on each."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Tag = cls.env["project.tags"]
        cls.bizdev = Tag.create({"name": "WC bizdev", "is_work_category": True, "work_category_sequence": 5})
        cls.client = Tag.create({"name": "WC client", "is_work_category": True, "work_category_sequence": 10})
        cls.plain = Tag.create({"name": "WC plain"})
        Project = cls.env["project.project"]
        cls.project_bd = Project.create({"name": "WC bizdev project", "tag_ids": [(6, 0, cls.bizdev.ids)]})
        cls.project_client = Project.create({"name": "WC client project", "tag_ids": [(6, 0, cls.client.ids)]})
        cls.project_none = Project.create({"name": "WC plain project", "tag_ids": [(6, 0, cls.plain.ids)]})

    def stored(self, record):
        """Category as written in the database, after a flush."""
        self.env.flush_all()
        self.env.cr.execute(
            f'SELECT work_category_id, work_category_origin FROM "{record._table}" WHERE id = %s', [record.id])
        return self.env.cr.fetchone()

    OLD = "2025-01-15 10:00:00"

    def age(self, *records):
        """Pretend the records were last modified long ago, by the root user."""
        self.env.flush_all()
        for record in records:
            self.env.cr.execute(
                f'UPDATE "{record._table}" SET write_date = %s, write_uid = 1 WHERE id = %s', [self.OLD, record.id])
        self.env.invalidate_all()

    def last_modified(self, record):
        """(write_date, write_uid) as written in the database, after a flush."""
        self.env.flush_all()
        self.env.cr.execute(f'SELECT write_date::text, write_uid FROM "{record._table}" WHERE id = %s', [record.id])
        return self.env.cr.fetchone()
