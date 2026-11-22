from . import bf_note_tag
from . import bf_note_link_mixin
from . import bf_note_link
from . import bf_note
from . import bf_note_share
from . import bf_note_activity_wizard
from . import bf_note_task_wizard
from . import bf_note_reroute
from . import res_partner
from . import project_task
from . import project_project
from . import crm_lead
from . import res_users
from . import onboarding_onboarding
from . import bf_note_mobile
from . import bf_note_color
# Après bf_note_mobile : les deux surcharges appellent `_mobile_touch` et
# `_mobile_exige_de_retirer`, qui y sont définis.
from . import ir_attachment
from . import mail_activity
