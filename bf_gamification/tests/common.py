"""Aides communes des essais de Fox Quest et de ses ponts."""
from odoo.addons.bf_gamification.models import gamification_profile
from odoo.addons.bf_gamification.models.gamification_profile import IMPERSONATION_SESSION_KEY


class FakeRequest:
    """Une requête HTTP pour les essais : un TransactionCase n'en a pas, alors que
    Fox Quest lit la requête (geste de la personne) et sa session (usurpation).

    La session est un simple dictionnaire ; la charge de `bf_impersonate`, elle,
    n'a besoin que d'exister : Fox Quest ne lit que la clé de session, sans
    dépendre de ce module."""

    def __init__(self, session=None):
        self.session = {} if session is None else session

    def __bool__(self):
        return True


def in_a_request(test, impersonated=False):
    """Joue la suite de l'essai « dans une requête » ; `impersonated` : sous
    l'usurpation du support (`bf_impersonate`). Remplace `request` du profil."""
    session = {IMPERSONATION_SESSION_KEY: {"mode": "write", "target_uid": 0}} if impersonated else {}
    test.patch(gamification_profile, "request", FakeRequest(session))
