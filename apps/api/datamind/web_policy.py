"""Build bounded public search queries from the current question only."""

import re


def automatic_query(question: str, sources: list[dict]) -> str:
    # Never forward conversation history, retrieved text, generated queries or database output.
    # Ambiguous/private questions stay local; the user can ask a separate public-topic question.
    if re.search(
        r"(?i)\b(our|my|internal|confidential|private|password|token|api.key|customer_id|revenue|invoices?|contracts?|tickets?|customers?|sales|selected|documents?|files?|employees?)\b",
        question,
    ):
        return ""
    if re.search(r"(?i)[\w.+-]+@[\w.-]+|\b(?:C\d{3}|CRM-\d+|T\d{3})\b|AIza[\w-]+|://[^\s]*[@]", question):
        return ""
    if any(s["name"].lower() in question.lower() for s in sources if s.get("name")):
        return ""
    public_intent = re.search(
        r"(?i)\b(latest|news|today|current|recent|competitor|competitors|competitive intelligence|market trends|public web|search online)\b",
        question,
    )
    public_fact = re.match(r"(?i)\s*(what (is|are)|who (is|are)|when (did|was)|explain|how does)\b", question)
    if not public_intent and (sources or not public_fact):
        return ""
    return " ".join(question.split())[:400]
