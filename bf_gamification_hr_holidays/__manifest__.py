{
    "name": "Fox Quest : congés de constance dans Congés",
    "summary": "Une journée de congé de constance approuvée devient une allocation de congé",
    # 18.0.1.0.0: first version. When a manager approves the request opened
    #   by Fox Quest's constance step, an allocation of the type chosen in the
    #   settings is created in Time Off for the employee. It is validated at
    #   once if the approver has that right in Time Off; otherwise it waits for
    #   a time off officer. The allocation linked to a request cannot be
    #   changed by hand, is created without the caller's context, and approving
    #   or refusing is refused under support impersonation.
    "version": "18.0.1.0.0",
    "category": "Human Resources",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    "auto_install": True,
    "depends": [
        "bf_gamification",
        "hr_holidays",
    ],
    "data": [
        "views/res_config_settings_views.xml",
        "views/gamification_reward_claim_views.xml",
    ],
}
