{
    "name": "BF Irregularities — Projects and timesheets",
    "summary": "Detectors for tasks stuck waiting, orphaned tasks and days without a timesheet",
"description": """
Irregularities over projects and timesheets
===========================================

Four detectors for ``bf_irregularity``, and no screen of its own:

* a task left in a waiting state past a threshold, unless a deadline or a
  planned activity already arranges the follow-up;
* an open task in an archived project;
* an open task with nobody assigned (shipped inactive);
* a working day with no timesheet, read from the employee's working calendar,
  skipping approved time off when ``hr_holidays`` is installed.

Each finding is owned by the person responsible for the record: the first active
assignee, else the project manager, else the detector's fallback owner.
    """,
    "version": "18.0.1.2.0",
    "category": "Project",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_irregularity", "bf_task_waiting_states", "hr_timesheet"],
    "data": [
        "data/bf_irregularity_detector_data.xml",
    ],
    "installable": True,
    "auto_install": True,
}
