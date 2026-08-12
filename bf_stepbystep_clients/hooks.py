"""Post-install hook for bf_stepbystep_clients.

On fresh install, populate progression_step_number / progression_step_name on
project.task.type rows by matching their names against the legacy Loi 25
keyword map. Idempotent: skips stages where progression_step_number is already
non-zero.

Module 9 (Formation) is intentionally NOT migrated — it was removed from the
progression workflow (cf. plan QW1).
"""
import logging

_logger = logging.getLogger(__name__)


# Legacy Loi 25 keyword map (subset, Formation excluded).
# Key: lowercase keyword found in stage name. Value: (step_number, step_name).
# Matched via `in`, so order matters: more specific first.
_LEGACY_KEYWORD_MAP = [
    # Module 0: Démarrage
    ("démarrage", (0, "Démarrage et mobilisation")),
    ("offre de services", (0, "Démarrage et mobilisation")),
    ("communications initiales", (0, "Démarrage et mobilisation")),
    ("entente de confidentialité", (0, "Démarrage et mobilisation")),
    ("lancement", (0, "Démarrage et mobilisation")),
    ("honoraires", (0, "Démarrage et mobilisation")),
    # Module 1: Audit initial
    ("formulaires initiaux", (1, "Audit initial et EFVP")),
    ("audit initial", (1, "Audit initial et EFVP")),
    ("efvp", (1, "Audit initial et EFVP")),
    # Module 2: Politique
    ("politique de confidentialité", (2, "Politique de confidentialité")),
    # Module 3: RPRP
    ("rprp", (3, "Encadrement du RPRP")),
    ("encadrement", (3, "Encadrement du RPRP")),
    ("responsable", (3, "Encadrement du RPRP")),
    ("désignation", (3, "Encadrement du RPRP")),
    # Module 4: Audit TI — must match "audit ti" BEFORE generic "audit"
    ("audit ti", (4, "Audit TI approfondi")),
    ("sécurité numérique", (4, "Audit TI approfondi")),
    ("audit approfondi", (4, "Audit TI approfondi")),
    ("parc", (4, "Audit TI approfondi")),
    # Module 5: Consentement
    ("droits individuels", (5, "Formulaires de consentement")),
    ("consentement", (5, "Formulaires de consentement")),
    # Module 6: Gestion documentaire
    ("gestion documentaire", (6, "Calendrier de conservation")),
    ("calendrier de conservation", (6, "Calendrier de conservation")),
    ("conservation", (6, "Calendrier de conservation")),
    # Module 7: Incidents
    ("incidents", (7, "Registre des incidents")),
    ("registre des incidents", (7, "Registre des incidents")),
    ("inscription au registre", (7, "Registre des incidents")),
    # Module 8: Gouvernance
    ("gouvernance", (8, "Plan de gouvernance")),
    ("plan de gouvernance", (8, "Plan de gouvernance")),
    ("plan intégré", (8, "Plan de gouvernance")),
    # NOTE: Module 9 (Formation) intentionally omitted — removed in QW1.
    # Module 10: Clôture — kept as step 9 in the new numbering, no gap.
    ("clôture", (9, "Clôture du mandat")),
    ("clôture du mandat", (9, "Clôture du mandat")),
]


def _stage_to_step(stage_name):
    """Map a stage name to (step_number, step_name), or None if unknown."""
    if not stage_name:
        return None
    name_lower = stage_name.lower().strip()
    for keyword, (step_num, step_name) in _LEGACY_KEYWORD_MAP:
        if keyword in name_lower:
            return (step_num, step_name)
    return None


def post_init_hook(env):
    """Populate progression_step_number / progression_step_name on existing
    project.task.type rows. Idempotent.

    Skips stages where progression_step_number is already non-zero (i.e.
    manually configured before this hook ran).
    """
    Stage = env["project.task.type"]
    stages = Stage.search([("progression_step_number", "=", 0)])

    matched = 0
    unmatched = []
    for stage in stages:
        # name on project.task.type is a translated jsonb; the ORM resolves
        # to the user's lang. Fall back to raw value if needed.
        name = stage.name or ""
        result = _stage_to_step(name)
        if result is None:
            unmatched.append((stage.id, name))
            continue
        step_num, step_name = result
        stage.write({
            "progression_step_number": step_num,
            "progression_step_name": step_name,
        })
        matched += 1

    _logger.info(
        "bf_stepbystep_clients post_init_hook: %d stages mapped, %d unmatched.",
        matched, len(unmatched),
    )
    if unmatched:
        _logger.info(
            "bf_stepbystep_clients unmatched stages (configure manually):\n%s",
            "\n".join(f"  - id={sid}: {name!r}" for sid, name in unmatched),
        )
