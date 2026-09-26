"""Node types and entity categories: labels, colours (Google palette) and icons
(Material Symbols names).  The UI legend and filters are driven from here."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Kind:
    key: str
    label: str
    group: str
    color: str
    icon: str
    layer: str = "entity"


# ---- structural node types (the "flow chart" of the conversation) -------
NODE_TYPES: list[Kind] = [
    Kind("conversation", "Conversation", "Structure", "#3C4043", "forum", "dialogue"),
    Kind("user", "User", "Structure", "#1A73E8", "person", "dialogue"),
    Kind("assistant", "LLM", "Structure", "#188038", "smart_toy", "dialogue"),
    Kind("system", "System", "Structure", "#80868B", "settings", "dialogue"),
    Kind("thought", "Thought", "Structure", "#9334E6", "psychology", "thought"),
    Kind("tool_call", "Tool call", "Structure", "#E8710A", "build", "action"),
    Kind("tool_arg", "Tool argument", "Structure", "#B06000", "tune", "action"),
    Kind("tool_result", "Tool result", "Structure", "#129EAF", "output", "action"),
    Kind("tool_hub", "Tool (all calls)", "Structure", "#E52592", "handyman", "action"),
]

# ---- entity categories ---------------------------------------------------
CATEGORIES: list[Kind] = [
    # technical
    Kind("file_path", "File path", "Technical", "#5F6368", "description"),
    Kind("url", "URL", "Technical", "#185ABC", "link"),
    Kind("domain", "Domain", "Technical", "#669DF6", "language"),
    Kind("ip", "IP address", "Technical", "#D93025", "lan"),
    Kind("host", "Host:port", "Technical", "#EE675C", "dns"),
    Kind("email", "Email", "Technical", "#B31412", "mail"),
    Kind("hash", "Hash", "Technical", "#681DA8", "tag"),
    Kind("uuid", "UUID", "Technical", "#AF5CF7", "fingerprint"),
    Kind("cve", "CVE", "Technical", "#A50E0E", "gpp_bad"),
    Kind("version", "Version", "Technical", "#098591", "new_releases"),
    Kind("env_var", "Variable / constant", "Technical", "#137333", "data_object"),
    Kind("error", "Error type", "Technical", "#C5221F", "error"),
    Kind("code", "Code", "Technical", "#3C4043", "code"),
    Kind("software", "Software / tech", "Technical", "#1967D2", "terminal"),
    Kind("cloud", "Cloud resource", "Technical", "#4285F4", "cloud"),
    Kind("mac", "MAC address", "Technical", "#F28B82", "router"),
    # people & organisations
    Kind("person", "Person", "People & orgs", "#E52592", "person"),
    Kind("role", "Role", "People & orgs", "#FF63B8", "badge"),
    Kind("organization", "Organization", "People & orgs", "#C5221F", "corporate_fare"),
    Kind("group", "Group / nationality", "People & orgs", "#F538A0", "groups"),
    Kind("mention", "@mention", "People & orgs", "#FA7B17", "alternate_email"),
    # places
    Kind("place", "Place", "Places", "#D93025", "place"),
    Kind("facility", "Facility", "Places", "#EE675C", "apartment"),
    Kind("geo", "Coordinates", "Places", "#A50E0E", "my_location"),
    # domain things
    Kind("food", "Food / ingredient", "Things", "#F29900", "restaurant"),
    Kind("vehicle", "Vehicle", "Things", "#185ABC", "directions_car"),
    Kind("device", "Device", "Things", "#12B5CB", "devices"),
    Kind("tool", "Tool / utensil", "Things", "#C26401", "hardware"),
    Kind("clothing", "Clothing", "Things", "#AF5CF7", "checkroom"),
    Kind("weapon", "Weapon", "Things", "#5F6368", "shield"),
    Kind("animal", "Animal", "Things", "#5BB974", "pets"),
    Kind("plant", "Plant", "Things", "#137333", "eco"),
    Kind("body", "Body part", "Things", "#FF8BCB", "accessibility"),
    Kind("medical", "Medical", "Things", "#D01884", "medical_services"),
    Kind("substance", "Substance", "Things", "#098591", "science"),
    Kind("product", "Product", "Things", "#1A73E8", "inventory_2"),
    Kind("document", "Document", "Things", "#80868B", "article"),
    Kind("activity", "Activity / sport", "Things", "#34A853", "sports_soccer"),
    Kind("color", "Colour", "Things", "#FCC934", "palette"),
    Kind("emotion", "Emotion", "Things", "#F439A0", "mood"),
    # time & numbers
    Kind("date", "Date", "Time & numbers", "#9AA0A6", "event"),
    Kind("time", "Time", "Time & numbers", "#9AA0A6", "schedule"),
    Kind("money", "Money", "Time & numbers", "#188038", "payments"),
    Kind("finance", "Finance", "Time & numbers", "#188038", "account_balance"),
    Kind("quantity", "Quantity", "Time & numbers", "#B80672", "straighten"),
    Kind("measure", "Unit", "Time & numbers", "#B80672", "square_foot"),
    Kind("number", "Number", "Time & numbers", "#BDC1C6", "pin"),
    Kind("percent", "Percent", "Time & numbers", "#BDC1C6", "percent"),
    Kind("phone", "Phone", "Time & numbers", "#7CB342", "call"),
    # other
    Kind("event", "Event", "Other", "#E37400", "celebration"),
    Kind("work", "Work of art", "Other", "#9334E6", "auto_stories"),
    Kind("law", "Law", "Other", "#5F6368", "gavel"),
    Kind("language", "Language", "Other", "#1E8E3E", "translate"),
    Kind("tag", "#tag", "Other", "#FA903E", "sell"),
    Kind("concept", "Concept", "Other", "#A8C7FA", "lightbulb"),
]

CATEGORY_KEYS = {c.key for c in CATEGORIES}

NER_TO_CATEGORY = {
    "PERSON": "person",
    "NORP": "group",
    "FAC": "facility",
    "ORG": "organization",
    "GPE": "place",
    "LOC": "place",
    "PRODUCT": "product",
    "EVENT": "event",
    "WORK_OF_ART": "work",
    "LAW": "law",
    "LANGUAGE": "language",
    "DATE": "date",
    "TIME": "time",
    "PERCENT": "percent",
    "MONEY": "money",
    "QUANTITY": "quantity",
    "ORDINAL": "number",
    "CARDINAL": "number",
}

# Categories whose surface form is case sensitive / should not be lemmatised
LITERAL_CATEGORIES = {
    "file_path", "url", "hash", "uuid", "env_var", "code", "version", "cve", "cloud", "error",
}


def as_json() -> dict:
    return {
        "node_types": [asdict(k) for k in NODE_TYPES],
        "categories": [asdict(k) for k in CATEGORIES],
    }
