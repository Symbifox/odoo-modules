{
    "name": "Symbifox Keys",
    "version": "18.0.1.0.13",
    "category": "Human Resources",
    "summary": "Who holds which key, card or remote of the premises, which doors each one "
               "opens, and nothing forgotten when someone leaves",
    "description": """
Keys of the premises
====================

Physical keys, access cards, fobs and remote controls: who holds what, since
when, which doors each one opens, and what must come back when someone leaves.

- three separate things, as key control policies require: the key type (its
  symbol in the master key system, e.g. AA1), each physical copy (its stamped
  number, its holder, its state) and the door (its cylinder). The doors a key
  opens are computed from the master key hierarchy: a master opens every door
  of the keys under it, a cylinder marked "not master keyed" only opens with
  its own key, cross keying is flagged;
- the holder of a copy is read from its open hand-out, never stored beside
  it: one source of truth, the one you trust in a dispute;
- hand-out from the screen, with a signature, an expected return date (the
  end of the need) and an optional deposit. No direct transfer from one person
  to another: the copy comes back first;
- requests for a key, approved by the person responsible for the area. The
  request warns when the person already holds a key that opens those doors;
- lost or stolen: the hand-out is closed, an incident lists the compromised
  doors, and the key manager gets the decision to make (rekey those doors, or
  deactivate the card in the access system);
- departures: when an employee is archived or gets a departure date, an
  activity "Recover" is created for each item still held, and closes itself
  when the item comes back;
- periodic audits: holders confirm what they still have from "Keys to confirm", the
  rest becomes a list of discrepancies;
- labels carry a blind code and the copy number, never the door, the level or
  the address;
- Quebec Law 25: the name, phone and employer of a person without an account,
  and the signature, are erased after a retention period you set.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",  # Business Source License 1.1, see LICENSE
    "depends": ["hr", "mail"],
    "data": [
        "security/bf_key_security.xml",
        "security/ir.model.access.csv",
        "data/bf_key_data.xml",
        "report/bf_key_reports.xml",
        "wizard/bf_key_wizard_views.xml",
        "views/bf_key_area_views.xml",
        "views/bf_key_system_views.xml",
        "views/bf_key_type_views.xml",
        "views/bf_key_door_views.xml",
        "views/bf_key_copy_views.xml",
        "views/bf_key_assignment_views.xml",
        "views/bf_key_request_views.xml",
        "views/bf_key_incident_views.xml",
        "views/bf_key_audit_views.xml",
        "views/bf_key_dashboard_views.xml",
        "views/hr_employee_views.xml",
        "views/res_config_settings_views.xml",
        "views/bf_key_menus.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
