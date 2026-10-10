from . import models
from . import wizard


def uninstall_hook(env):
    """Les activités des rappels d'abord : sinon la suppression de leur type échoue
    sur une clé étrangère (erreur au journal, état final propre quand même)."""
    env["mail.activity"].sudo().with_context(active_test=False).search(
        [("res_model", "=", "bf.credit.reminder")]).unlink()
