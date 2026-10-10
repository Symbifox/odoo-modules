"""18.0.1.0.1 : un rappel n'a plus aucun abonné. La 1.0.0 abonnait son propriétaire
(et la tâche planifiée l'abonnait à chaque activité) : ces lignes s'en vont."""


def migrate(cr, version):
    if not version:
        return
    cr.execute("DELETE FROM mail_followers WHERE res_model = 'bf.credit.reminder'")
