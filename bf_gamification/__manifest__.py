{
    "name": "Fox Quest",
    "summary": "Syst\u00e8me de gamification avec XP, niveaux, badges et r\u00e9compenses",
    # 18.0.2.6.1: XP earned and XP balance are two numbers. Level, rank and
    #   threshold badges follow the XP earned; a reward draws on the balance.
    #   A user no longer sets the state or approval of their own claim, and no
    #   longer writes profiles (XP credits run as sudo).
    # 18.0.2.6.2: resetting everyone's progress (raw SQL) requires an
    #   administrator, also when the method is reached through RPC.
    # 18.0.2.7.0: badge categories and levels take a free color (bf_color).
    # 18.0.2.7.1: the displayed color opens "My color"; badge categories get
    #   a form of their own.
    # 18.0.3.0.0: the streak counts the person's working days, in their time
    #   zone (weekends, public holidays and declared absences pause it, they no
    #   longer break it), and is derived from the XP history, so an absence or
    #   a time off validated afterwards repairs it. The "Streak quotidien" rule
    #   finally pays; the "task before deadline" bonus is repaired (it never
    #   paid) and a task closed by its state counts too. Service and portal
    #   accounts are out of the game and of the leaderboard; the XP of a send
    #   goes to the human who sends. On-time sends (report within 24 h, agenda
    #   24 h ahead, activity by its deadline). The fox that congratulates when
    #   a work list empties. Constance days off, off by default, opted into by
    #   the employee, approved by a human. Only the person's own gesture, in a
    #   request, counts: not a cron, a colleague, a robot or support
    #   impersonation. The XP journal sets its own date and cannot be
    #   backdated, rewritten or deleted by hand; nothing Fox Quest creates
    #   takes defaults from the caller's context; managers no longer read the
    #   "caught up" days; the reset keeps constance time off.
    # 18.0.3.0.1: the reward claim has a readable name (reward – person): the
    #   activity email sent to the approver, its "Document" column and the reply-to
    #   address showed the technical model name.
    # 18.0.3.0.2: a zero, an empty value or a negative number in the constance
    #   settings (step, days per step, yearly cap) reads as the default, whichever
    #   way the Odoo core stores a zero.
    # 18.0.3.1.0: daily challenges (three per working day, drawn from what the person can do,
    #   the first pays double) and a stamp card (five stamps pay a bonus); next goals (the
    #   closest badges with a counter) on the dashboard and in the top bar; a worn title (the
    #   name of a badge you earned); a word from the fox on every finished challenge. Only the
    #   person's own gesture in their own web request counts: not a scheduled job, a robot, a
    #   colleague, the support, an XML-RPC call, nor what the mailbox engines process by
    #   themselves when the person clicks "Synchroniser maintenant". The top-bar call is two to
    #   three times lighter (badge progress without recounting earned badges, the week's XP as
    #   a sum). The switches in Settings can now be turned off.
    "version": "18.0.3.1.0",
    "category": "Human Resources",
    'website': 'https://symbifox.com',
    "author": "Les services de consultation Blue Fox, Inc.",
    'license': 'Other proprietary',
    "application": True,
    "installable": True,
    "post_init_hook": "_post_init_backfill",
    "depends": [
        "bf_color",
        "base",
        "mail",
        "bus",
        "hr_timesheet",
        "project",
        "web",
        "project_knowledge_matrix",
        # `corporate.resolution` a quitté project_knowledge_matrix à sa
        # 18.0.12.0.0 : sans cette ligne, Fox Quest se charge avant le
        # module qui porte le modèle qu'il hérite.
        "bf_corporate_governance",
        "hosting_management",
        "helpdesk_mgmt",
        "bf_onboarding_base",
        "bf_meeting",
        "bf_email_management",
    ],
    "data": [
        # Security
        "security/gamification_security.xml",
        "security/ir.model.access.csv",
        # Data
        "data/gamification_level_data.xml",
        "data/gamification_badge_category_data.xml",
        "data/gamification_xp_rule_data.xml",
        "data/gamification_badge_data.xml",
        "data/gamification_constance_data.xml",
        "data/gamification_challenge_data.xml",
        "data/gamification_cron.xml",
        "data/bf_onboarding.xml",
        # Wizard
        "wizard/grant_badge_wizard_views.xml",
        # Views
        "views/gamification_level_views.xml",
        "views/gamification_badge_category_views.xml",
        "views/gamification_badge_views.xml",
        "views/gamification_user_badge_views.xml",
        "views/gamification_profile_views.xml",
        "views/gamification_reward_views.xml",
        "views/gamification_reward_claim_views.xml",
        "views/gamification_xp_rule_views.xml",
        "views/gamification_challenge_views.xml",
        "views/gamification_xp_transaction_views.xml",
        "views/gamification_dashboard_views.xml",
        "views/res_config_settings_views.xml",
        "views/res_users_views.xml",
        "views/menu_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_gamification/static/src/scss/gamification.scss",
            "bf_gamification/static/src/scss/achievement_popup.scss",
            "bf_gamification/static/src/scss/levelup_popup.scss",
            "bf_gamification/static/src/js/gamification_bus_service.js",
            "bf_gamification/static/src/js/achievement_popup.js",
            "bf_gamification/static/src/js/levelup_popup.js",
            "bf_gamification/static/src/js/gamification_systray.js",
            "bf_gamification/static/src/js/gamification_dashboard.js",
            "bf_gamification/static/src/xml/achievement_popup.xml",
            "bf_gamification/static/src/xml/levelup_popup.xml",
            "bf_gamification/static/src/xml/gamification_systray.xml",
            "bf_gamification/static/src/xml/gamification_dashboard.xml",
        ],
    },
}
