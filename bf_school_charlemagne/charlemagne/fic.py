"""The requests for a file (transaction type FI, service FIC), from section 6.34 to 6.45 of the guide.

A request asks the Ministry to produce a list, a report or a feedback file for the school. Only the
parameters are defined here; the produced files come back by SFTP, and the school reads them.
"""
from .xml_spec import E, Transaction

#: code -> (title, spec). The element order is the guide's.
FIC = {
    "OCC": ("Correction keys of the exams", Transaction("TRANSACTION_FI_OCC", [
        E("AN_EPREU", "N", 4, True), E("SESN_EPREU", "N", 2, True), E("CD_COURS", "A", 10, False)])),
    "LEC": ("List of failures by course", Transaction("TRANSACTION_FI_LEC", [
        E("AN_EPREU", "N", 4, True), E("SESN_EPREU", "N", 2, True), E("CD_ORGNS", "N", 6, True),
        E("FORMT", "A", 3, True)])),
    "LEE": ("List of failures by student", Transaction("TRANSACTION_FI_LEE", [
        E("AN_EPREU", "N", 4, True), E("SESN_EPREU", "N", 2, True), E("CD_ORGNS", "N", 6, True),
        E("FORMT", "A", 3, True)])),
    "RCM": ("Conversion and moderation tables", Transaction("TRANSACTION_FI_RCM", [
        E("AN_EPREU", "N", 4, True), E("SESN_EPREU", "N", 2, True), E("CD_ORGNS", "N", 6, True),
        E("FORMT", "A", 3, True), E("CD_EPREU", "A", 10, False)])),
    "RIB": ("Feedback of the data at the headcount review", Transaction("TRANSACTION_FI_RIB", [
        E("CD_ORGNS", "N", 6, True), E("AN_SCOLR_PPS", "N", 8, True), E("TYPE_DENMB", "A", 2, True),
        E("TYPE_FORMT", "A", 2, True), E("SECTR_ENSEI", "A", 2, False)])),
    "RID": ("Operational feedback of the declarations", Transaction("TRANSACTION_FI_RID", [
        E("CD_ORGNS", "N", 6, True), E("AN_SCOLR_PPS", "N", 8, True), E("TYPE_FORMT", "A", 2, True),
        E("SECTR_ENSEI", "A", 2, False), E("TYPE_DECLR", "A", 3, False)])),
    "RIR": ("Operational feedback of the learning results", Transaction("TRANSACTION_FI_RIR", [
        E("CD_ORGNS", "N", 6, True), E("TYPE_FORMT", "A", 2, True), E("SECTR_ENSEI", "A", 2, False),
        E("AN_EPREU", "N", 4, True), E("SESN_EPREU", "N", 2, True)])),
    "RAS": ("Sanctioned learning results", Transaction("TRANSACTION_FI_RAS", [
        E("CD_ORGNS", "N", 6, True), E("TYPE_FORMT", "A", 2, True), E("SECTR_ENSEI", "A", 2, False),
        E("AN_SCOLR_PPS", "A", 8, True)])),
    "RIM": ("Mentions", Transaction("TRANSACTION_FI_RIM", [
        E("CD_ORGNS", "N", 6, True), E("TYPE_FORMT", "A", 2, True), E("SECTR_ENSEI", "A", 2, False),
        E("AN_SCOLR_PPS", "A", 8, True)])),
    "EPR": ("List of registrations to the ministerial exams", Transaction("TRANSACTION_FI_EPR", [
        E("CD_ORGNS", "N", 6, True), E("AN_EPREU", "N", 4, True), E("SESN_EPREU", "N", 2, True),
        E("CD_EPREU", "A", 10, False), E("NO_GROUP", "N", 2, False)])),
}
