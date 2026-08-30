from . import bf_email_account
# Identités d'expédition — _inherit bf.email.account, donc après lui.
from . import bf_email_identity
from . import bf_email
from . import bf_email_dashboard
from . import bf_email_rule_condition
from . import bf_email_auto_log
from . import bf_email_absence
from . import bf_email_rule
# Surface RPC de l'action cliente « Boîte de réception » — _inherit bf.email.
from . import bf_email_inbox
# Mobile API layer — must load after bf_email (it _inherit's it).
from . import bf_email_mobile_device
from . import bf_email_mobile_send
from . import push_transport
from . import popup_transport
from . import bf_email_mobile
from . import calendar_alarm_manager
from . import calendar_attendee
from . import calendar_event
# inherit_account_move / inherit_project_task / inherit_res_partner ont été
# retirés en 18.0.8.2.0 : ils surchargeaient `name_search` sous le drapeau de
# contexte `bf_email_reroute_search`, que seule l'ancienne liste déroulante du
# sorcier de re-routage posait. Le sélecteur `bf_chatter_target` ne passe plus
# par `name_search` sur la cible : les trois surcharges étaient devenues mortes.
from . import mail_compose_message
from . import mail_message
from . import mail_notification
from . import mail_scheduled_message
from . import onboarding_onboarding
from . import res_config_settings
from . import res_partner
from . import res_users
