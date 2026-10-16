"""The control reports the Ministry sends back, read.

After a transmission the Ministry returns a report of the command (CHACO...) and one of the requests and
their transactions (CHADE...), in the format asked for in the command (XML here), section 9 of the guide.
A transaction is accepted, accepted with a message (consequence A) or rejected (R), or held back (S).
"""
import xml.etree.ElementTree as ET

CONSEQUENCE = {"A": "accepted_message", "R": "rejected", "S": "suspended"}


def _text(node, path, default=""):
    found = node.find(path)
    return (found.text or "").strip() if found is not None and found.text else default


def _messages(parent, wrapper, item, number, consequence, text):
    out = []
    for message in parent.iterfind("%s/%s" % (wrapper, item)):
        out.append({
            "number": _text(message, number), "consequence": _text(message, consequence), "text": _text(message, text),
        })
    return out


def parse_command_report(xml_bytes):
    """The command report: messages and counters of the transmission as a whole."""
    root = ET.fromstring(xml_bytes)
    command = root.find("commande")
    if command is None:
        return None
    return {
        "number": command.get("noCommande", ""), "file": command.get("nomFichier", ""),
        "transmitter": _text(command, "codeOrganismeTransmetteur"),
        "requests": int(_text(command, "nbDemandesTransmises", "0") or 0),
        "accepted": int(_text(command, "nbDemandesAcceptees", "0") or 0),
        "refused": int(_text(command, "nbDemandesRefusees", "0") or 0),
        "suspended": int(_text(command, "nbDemandesSuspends", "0") or 0),
        "messages": _messages(command, "listeMessageCommande", "messageCommande", "noMessage", "consequence", "message"),
    }


def parse_request_report(xml_bytes):
    """The request report: each transaction by the id the school gave it, with its messages."""
    root = ET.fromstring(xml_bytes)
    out = {"command_number": root.get("noCommande", ""), "file": root.get("nomFichier", ""),
           "requests": [], "transactions": {}}
    for request in root.iterfind("Demande"):
        counters = {"transmitted": 0, "accepted_clean": 0, "accepted_message": 0, "rejected": 0}
        for counter in request.iterfind("listeCompteurTransaction/compteurTransaction"):
            for key, tag in (("transmitted", "nbTransactionsTransmises"), ("accepted_clean", "nbTransactionsAccepteesSansMessage"),
                             ("accepted_message", "nbTransactionsAccepteesAvecMessage"), ("rejected", "nbTransactionsRefusees")):
                counters[key] += int(_text(counter, tag, "0") or 0)
        out["requests"].append(dict(counters, **{
            "number": _text(request, "noDemande"), "type": _text(request, "typeTransaction"),
            "organism": _text(request, "codeOrganismeTransmis"),
            "messages": _messages(request, "listeMessageDemande", "messageDemande", "noMessageDemande",
                                  "consequenceDemande", "descriptionMessageDemande")}))
    for transaction in root.iterfind("listeTransaction/transactionSysteme"):
        identity = _text(transaction, "identificationTransaction")
        messages = _messages(transaction, "listeMessageTransactionSysteme", "messageTransactionSysteme",
                             "noMessageTransaction", "consequenceTransaction", "descriptionMessageTransactionSysteme")
        out["transactions"][identity] = {"code": _text(transaction, "codeTransactionSysteme"), "messages": messages}
    return out


def transaction_outcome(messages):
    """accepted / accepted_message / rejected / suspended from the messages of one transaction."""
    consequences = {m["consequence"] for m in messages}
    if "R" in consequences:
        return "rejected"
    if "S" in consequences:
        return "suspended"
    if "A" in consequences:
        return "accepted_message"
    return "accepted"
