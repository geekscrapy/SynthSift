"""Generate the fake sample transcripts shipped in samples/.

    uv run python scripts/generate_samples.py

Writes samples/transcripts/<host>/<user>/<harness>/<file> and bundles them into
samples/synthsift-samples.zip.  Output is deterministic.
"""

from __future__ import annotations

import json
import random
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "samples"


class Chat:
    """Tiny builder for the synthsift.example/v1 format."""

    def __init__(self, session_id: str, title: str, model: str, start: str, system: str | None = None):
        self.t = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
        self.doc = {"format": "synthsift.example/v1", "session_id": session_id, "title": title, "model": model,
                    "started_at": self.t.isoformat().replace("+00:00", "Z"), "messages": []}
        self.n = 0
        if system:
            self.doc["messages"].append({"role": "system", "content": system})

    def _ts(self, secs: int = 20) -> str:
        self.t += timedelta(seconds=secs)
        return self.t.isoformat().replace("+00:00", "Z")

    def user(self, text: str) -> "Chat":
        self.doc["messages"].append({"role": "user", "content": text, "timestamp": self._ts(60)})
        return self

    def assistant(self, *blocks) -> "Chat":
        self.doc["messages"].append({"role": "assistant", "content": list(blocks), "timestamp": self._ts()})
        return self

    def tool(self, call_id: str, name: str, content: str, error: bool = False) -> "Chat":
        msg = {"role": "tool", "tool_call_id": call_id, "name": name, "content": content, "timestamp": self._ts(3)}
        if error:
            msg["is_error"] = True
        self.doc["messages"].append(msg)
        return self

    def call(self, name: str, **args):
        self.n += 1
        return {"type": "tool_call", "id": f"call_{self.n:03d}", "name": name, "arguments": args}


def think(text: str) -> dict:
    return {"type": "thinking", "text": text}


def say(text: str) -> dict:
    return {"type": "text", "text": text}


# --------------------------------------------------------------------------
def garlic_bread() -> dict:
    c = Chat("gb-7f3a", "Garlic bread for Saturday", "chef-model-2", "2026-03-14T17:02:00",
             "You are a friendly cooking assistant with web search and unit conversion tools.")
    c.user("Hi! I'm hosting Maria and Tom on Saturday and want to make proper garlic bread. "
           "I have a sourdough loaf, butter, a whole head of garlic, parsley and some parmesan. "
           "Do I need anything else?")
    s1 = c.call("web_search", query="best garlic bread recipe sourdough butter parmesan", max_results=3)
    c.assistant(
        think("The user has most ingredients. Classic garlic butter needs salt and maybe olive oil. "
              "Let me check a reliable recipe for oven temperature and timing before answering."),
        say("Great base already! Let me quickly check a well-tested recipe for timings."),
        s1,
    )
    c.tool(s1["id"], "web_search",
           "1. Serious Eats – The Best Garlic Bread: roast garlic, mix with softened butter, olive oil, "
           "parmesan and parsley; bake at 425°F for 10 minutes then broil 1–2 minutes.\n\n"
           "2. BBC Good Food – Sourdough garlic bread: 100g butter, 3 garlic cloves, pinch of sea salt, "
           "bake 200°C for 12 minutes wrapped in foil.\n\n"
           "3. NYT Cooking – Garlic bread with mozzarella.")
    conv = c.call("unit_convert", value=425, from_unit="fahrenheit", to_unit="celsius")
    c.assistant(
        think("The user is probably in Europe given 'parmesan' spelling doesn't tell much, but BBC uses Celsius. "
              "Convert 425°F so both are shown."),
        conv,
    )
    c.tool(conv["id"], "unit_convert", "425 fahrenheit = 218.3 celsius")
    c.assistant(say(
        "You only need two extras: **flaky sea salt** and a splash of **olive oil**.\n\n"
        "1. Soften 100 g butter and mash in 4 grated garlic cloves, 2 tbsp chopped parsley, "
        "30 g grated parmesan, a pinch of salt and 1 tbsp olive oil.\n"
        "2. Slice the sourdough loaf almost all the way through and spread the garlic butter between slices.\n"
        "3. Wrap in foil and bake at 220 °C (425 °F) for 10 minutes, then open the foil and broil for 2 minutes.\n\n"
        "Maria and Tom will love it."))
    c.user("Tom is lactose intolerant. What can replace the butter?")
    c.assistant(
        think("Lactose intolerance – ghee is nearly lactose free, or use olive oil entirely, or a vegan butter. "
              "Parmesan is naturally very low in lactose, so it can usually stay."),
        say("Swap the butter for **extra virgin olive oil** (use about 80 ml) or **ghee**, which is almost "
            "lactose-free. Aged parmesan contains very little lactose, so most people tolerate it. "
            "If you want to be extra safe, use nutritional yeast instead of the cheese."),
    )
    return c.doc


def road_trip() -> dict:
    c = Chat("trip-2231", "Portugal road trip", "travel-agent-1", "2026-05-02T09:15:00")
    c.user("Plan a 5 day road trip from Lisbon to Porto for me (Ana) and Marco in late June. "
           "We'll rent a Toyota RAV4 hybrid. Budget is about €1,200 excluding flights.")
    r1 = c.call("maps_route", origin="Lisbon, Portugal", destination="Porto, Portugal",
                waypoints=["Sintra", "Óbidos", "Nazaré", "Coimbra"], vehicle="car")
    w1 = c.call("weather_lookup", location="Lisbon", month="June")
    c.assistant(
        think("Classic coastal route: Sintra, Óbidos, Nazaré, Coimbra, then Porto. "
              "Check the driving distance and typical June weather first. "
              "Maybe also consider Aveiro but it could be too much for 5 days."),
        r1, w1)
    c.tool(r1["id"], "maps_route",
           "Total distance 412 km, driving time 5h 10m.\nLisbon → Sintra 28 km\nSintra → Óbidos 72 km\n"
           "Óbidos → Nazaré 45 km\nNazaré → Coimbra 110 km\nCoimbra → Porto 123 km\nTolls approx €38.")
    c.tool(w1["id"], "weather_lookup", "Lisbon in June: average high 26°C, low 17°C, 1 rainy day, sea 18°C.")
    c.assistant(say(
        "Here's a relaxed plan (≈412 km, 5 h of driving in total):\n\n"
        "Day 1 – Pick up the RAV4 at Lisbon airport, drive to Sintra (Pena Palace, Quinta da Regaleira).\n\n"
        "Day 2 – Óbidos walled town, then Nazaré for the giant-wave cliffs and grilled sardines.\n\n"
        "Day 3 – Coimbra: the university library (Biblioteca Joanina) and fado in the evening.\n\n"
        "Day 4 – Porto: Ribeira, Livraria Lello, port cellars in Vila Nova de Gaia.\n\n"
        "Day 5 – Douro Valley day trip, return the car in Porto.\n\n"
        "Rough costs for two: car hire €260, fuel €55, tolls €38, hotels €520, food €300 → about €1,173."))
    h1 = c.call("hotel_search", city="Porto", check_in="2026-06-25", nights=2, max_price_eur=130)
    c.user("Can you find a hotel in Porto near Ribeira for the last two nights?")
    c.assistant(think("Search near Ribeira, 2 nights from 25 June, keep under €130/night to stay within budget."), h1)
    c.tool(h1["id"], "hotel_search",
           "Pestana Vintage Porto – €128/night – Ribeira riverside – rating 4.6\n"
           "Casa da Companhia – €142/night – Baixa – rating 4.8\n"
           "Guest House Douro – €119/night – Ribeira – rating 4.7")
    c.assistant(say("Guest House Douro (€119/night, rating 4.7) is right on the Ribeira waterfront and keeps "
                    "you under budget. Pestana Vintage Porto is a good backup at €128."))
    return c.doc


def nginx_incident() -> dict:
    c = Chat("ops-5512", "502s from api gateway", "coder-model-3", "2026-06-10T02:41:00",
             "You are an SRE agent with shell access on buildbox-01. Be careful with production.")
    c.user("PagerDuty woke me up: api.example.com is returning 502 Bad Gateway since 02:30 UTC. "
           "nginx runs on 10.0.4.21, the upstream app is on 10.0.4.35:8080. Can you look?")
    b1 = c.call("bash", command="ssh ops@10.0.4.21 'sudo tail -n 20 /var/log/nginx/error.log'")
    c.assistant(
        think("502 from nginx means the upstream is failing or refusing connections. "
              "Start with the nginx error log, then check the app service on 10.0.4.35. "
              "I should not restart anything before understanding the cause."),
        say("Checking the nginx error log first."), b1)
    c.tool(b1["id"], "bash",
           "2026/06/10 02:30:11 [error] 1123#1123: *88121 connect() failed (111: Connection refused) while "
           "connecting to upstream, client: 203.0.113.9, server: api.example.com, request: \"GET /v2/orders "
           "HTTP/1.1\", upstream: \"http://10.0.4.35:8080/v2/orders\"\n"
           "2026/06/10 02:30:12 [error] 1123#1123: *88122 connect() failed (111: Connection refused) while "
           "connecting to upstream, client: 198.51.100.44, server: api.example.com")
    b2 = c.call("bash", command="ssh ops@10.0.4.35 'systemctl status orders-api --no-pager | head -n 15'")
    c.assistant(think("Connection refused → the orders-api process is probably down. Check systemd."), b2)
    c.tool(b2["id"], "bash",
           "● orders-api.service - Orders API (gunicorn)\n   Loaded: loaded (/etc/systemd/system/orders-api.service)\n"
           "   Active: failed (Result: exit-code) since Wed 2026-06-10 02:29:58 UTC\n"
           "  Process: 4411 ExecStart=/opt/orders/venv/bin/gunicorn app.main:app -b 0.0.0.0:8080 (code=exited, status=3)\n"
           "Jun 10 02:29:58 app-35 gunicorn[4411]: ModuleNotFoundError: No module named 'pydantic_settings'")
    r1 = c.call("read_file", path="/opt/orders/requirements.txt")
    c.assistant(
        think("ModuleNotFoundError for pydantic_settings. A deploy at ~02:29 probably upgraded pydantic to v2 "
              "without adding pydantic-settings. Check requirements.txt and the deploy log. "
              "Alternative: roll back to the previous release in /opt/orders/releases."),
        say("The service crashed on start with `ModuleNotFoundError: No module named 'pydantic_settings'`. "
            "Looking at the requirements."), r1)
    c.tool(r1["id"], "read_file", "fastapi==0.111.0\npydantic==2.7.1\ngunicorn==22.0.0\nuvicorn==0.29.0\n"
                                  "sqlalchemy==2.0.30\npsycopg[binary]==3.1.19")
    b3 = c.call("bash", command="ssh ops@10.0.4.35 'cat /opt/orders/DEPLOYED && ls /opt/orders/releases | tail -n 3'")
    c.assistant(think("pydantic 2.7.1 but no pydantic-settings pinned. Confirm which release is deployed."), b3)
    c.tool(b3["id"], "bash", "release=2026.06.10-0225 commit=9f2c1e7b4a0d3c5e8f7a6b1c2d3e4f5a6b7c8d9e by jenkins\n"
                            "2026.06.03-1410\n2026.06.08-0930\n2026.06.10-0225")
    c.assistant(say(
        "Root cause: release `2026.06.10-0225` (commit 9f2c1e7b4a0d3c5e8f7a6b1c2d3e4f5a6b7c8d9e) moved to "
        "pydantic 2.7.1, where `BaseSettings` lives in the separate `pydantic-settings` package, which isn't in "
        "/opt/orders/requirements.txt.\n\nOptions:\n1. Roll back to `2026.06.08-0930` (fastest, ~1 min).\n"
        "2. Install pydantic-settings==2.2.1 into /opt/orders/venv and restart.\n\nShall I roll back?"))
    c.user("Roll back now, then open a ticket for the dev team.")
    b4 = c.call("bash", command="ssh ops@10.0.4.35 'sudo /opt/orders/bin/rollback 2026.06.08-0930 && "
                                "sudo systemctl restart orders-api && curl -s -o /dev/null -w \"%{http_code}\" "
                                "http://localhost:8080/healthz'")
    c.assistant(b4)
    c.tool(b4["id"], "bash", "rolled back to 2026.06.08-0930\n200")
    t1 = c.call("create_ticket", project="ORD", title="orders-api fails to start: missing pydantic-settings",
                body="Release 2026.06.10-0225 upgraded pydantic to 2.7.1; BaseSettings moved to pydantic-settings. "
                     "Add pydantic-settings to requirements.txt. Rolled back to 2026.06.08-0930 at 02:52 UTC.",
                priority="P1", assignee="@dev-oncall")
    c.assistant(think("Health check returns 200. Verify nginx is happy too, then create the ticket."), t1)
    c.tool(t1["id"], "create_ticket", "Created ORD-4821 https://jira.example.com/browse/ORD-4821")
    c.assistant(say("Rolled back; `/healthz` returns 200 and the 502s stopped at 02:52 UTC. "
                    "Ticket ORD-4821 is assigned to @dev-oncall: https://jira.example.com/browse/ORD-4821"))
    return c.doc


def k8s_migration() -> dict:
    c = Chat("ops-5601", "Move reports job to EKS", "coder-model-3", "2026-06-12T13:05:00")
    c.user("We need to migrate the nightly reports cron job from the old EC2 box (i-0a1b2c3d4e5f67890) to our EKS "
           "cluster in eu-west-1. It needs the S3 bucket arn:aws:s3:::acme-reports-prod and the env vars "
           "REPORTS_DB_URL and SMTP_PASSWORD.")
    r1 = c.call("read_file", path="/etc/cron.d/reports")
    c.assistant(
        think("Plan: read the existing crontab entry and script, write a Kubernetes CronJob manifest, use IRSA "
              "for S3 access instead of static keys, and a Secret for SMTP_PASSWORD. Ask before applying."),
        r1)
    c.tool(r1["id"], "read_file", "# m h dom mon dow user command\n15 2 * * * reports /opt/reports/run.sh >> "
                                  "/var/log/reports.log 2>&1")
    r2 = c.call("read_file", path="/opt/reports/run.sh")
    c.assistant(r2)
    c.tool(r2["id"], "read_file", "#!/bin/bash\nset -euo pipefail\nexport AWS_REGION=eu-west-1\n"
                                  "python3 /opt/reports/generate.py --since yesterday --out /tmp/reports\n"
                                  "aws s3 sync /tmp/reports s3://acme-reports-prod/daily/")
    w1 = c.call("write_file", path="k8s/reports-cronjob.yaml", content=(
        "apiVersion: batch/v1\nkind: CronJob\nmetadata:\n  name: nightly-reports\n  namespace: reports\n"
        "spec:\n  schedule: \"15 2 * * *\"\n  jobTemplate:\n    spec:\n      template:\n        spec:\n"
        "          serviceAccountName: reports-sa\n          containers:\n          - name: reports\n"
        "            image: 123456789012.dkr.ecr.eu-west-1.amazonaws.com/reports:1.4.2\n"
        "            envFrom:\n            - secretRef:\n                name: reports-secrets\n"
        "          restartPolicy: OnFailure\n"))
    c.assistant(
        say("The job runs `generate.py` and syncs to `s3://acme-reports-prod/daily/`. I'll create a CronJob that "
            "uses the `reports-sa` service account (IRSA role for S3) and a Secret for REPORTS_DB_URL and "
            "SMTP_PASSWORD."), w1)
    c.tool(w1["id"], "write_file", "wrote 612 bytes to k8s/reports-cronjob.yaml")
    b1 = c.call("bash", command="kubectl --context eks-prod-euw1 apply --dry-run=server -f k8s/reports-cronjob.yaml")
    c.assistant(b1)
    c.tool(b1["id"], "bash", "Error from server (NotFound): namespaces \"reports\" not found", error=True)
    c.assistant(
        think("Namespace doesn't exist. Need to create namespace, service account with the IAM role annotation "
              "eks.amazonaws.com/role-arn, and the secret. Don't put SMTP_PASSWORD in git."),
        say("The `reports` namespace doesn't exist yet in eks-prod-euw1. I'll add a namespace and a ServiceAccount "
            "annotated with `arn:aws:iam::123456789012:role/reports-s3-writer`. The secret should be created from "
            "AWS Secrets Manager rather than committed. OK to proceed?"))
    c.user("Yes, go ahead, but use the External Secrets operator for the secret.")
    b2 = c.call("bash", command="kubectl --context eks-prod-euw1 apply -f k8s/namespace.yaml -f k8s/reports-sa.yaml "
                                "-f k8s/external-secret.yaml -f k8s/reports-cronjob.yaml")
    c.assistant(think("ExternalSecret referencing ClusterSecretStore aws-secrets-manager, key prod/reports."), b2)
    c.tool(b2["id"], "bash", "namespace/reports created\nserviceaccount/reports-sa created\n"
                            "externalsecret.external-secrets.io/reports-secrets created\n"
                            "cronjob.batch/nightly-reports created")
    c.assistant(say("Done. The CronJob `nightly-reports` runs at 02:15 UTC in eu-west-1. After the first "
                    "successful run you can stop the cron on i-0a1b2c3d4e5f67890."))
    return c.doc


def phishing_triage() -> dict:
    c = Chat("soc-9934", "Suspicious invoice email", "sec-analyst-2", "2026-04-22T08:10:00",
             "You are a SOC triage assistant. Never open attachments outside the sandbox.")
    c.user("Finance got an email from billing@micros0ft-support.com with attachment Invoice_8841.docm "
           "(sha256 3f79bb7b435b05321651daefd374cdc681dc06faa65e374e38337b88ca046dea). "
           "User jsmith@acme.io clicked the link to https://micros0ft-support.com/login?session=8841 "
           "from workstation FIN-WS-22 (10.20.5.77, MAC 00:1A:2B:3C:4D:5E). Triage please.")
    v1 = c.call("virustotal_lookup", sha256="3f79bb7b435b05321651daefd374cdc681dc06faa65e374e38337b88ca046dea")
    w1 = c.call("whois", domain="micros0ft-support.com")
    c.assistant(
        think("Typosquatted domain (zero instead of o). Macro-enabled .docm – likely a downloader. "
              "Check the hash reputation and domain age, then look for connections from 10.20.5.77 in proxy logs."),
        v1, w1)
    c.tool(v1["id"], "virustotal_lookup", "Detections: 41/68. Family: Emotet downloader. First seen 2026-04-21. "
                                          "Contacts: 185.220.101.47:443, 45.95.147.12:8080. Exploits CVE-2017-11882.")
    c.tool(w1["id"], "whois", "Domain: micros0ft-support.com\nCreated: 2026-04-19\nRegistrar: NameCheap, Inc.\n"
                              "Name servers: ns1.bulletproof-dns.ru, ns2.bulletproof-dns.ru")
    g1 = c.call("grep_logs", source="proxy", query="src=10.20.5.77 AND (dst=185.220.101.47 OR dst=45.95.147.12)",
                since="2026-04-22T00:00:00Z")
    c.assistant(think("Domain registered 3 days ago with Russian bulletproof DNS. Known Emotet. "
                      "Need to know if the workstation actually contacted the C2 addresses."), g1)
    c.tool(g1["id"], "grep_logs",
           "2026-04-22T07:58:03Z src=10.20.5.77 dst=185.220.101.47:443 bytes_out=5120 action=allowed\n"
           "2026-04-22T07:58:09Z src=10.20.5.77 dst=45.95.147.12:8080 bytes_out=210332 action=allowed")
    i1 = c.call("isolate_host", hostname="FIN-WS-22", reason="Emotet C2 beaconing, ticket SEC-2207")
    c.assistant(
        say("This is a confirmed compromise: FIN-WS-22 talked to Emotet C2 servers 185.220.101.47 and "
            "45.95.147.12 and uploaded ~210 KB. Isolating the host now."), i1)
    c.tool(i1["id"], "isolate_host", "FIN-WS-22 isolated via EDR at 2026-04-22T08:14:40Z")
    c.assistant(say(
        "Summary for SEC-2207:\n\n- Phishing from typosquatted micros0ft-support.com (registered 2026-04-19).\n"
        "- Attachment Invoice_8841.docm = Emotet downloader exploiting CVE-2017-11882.\n"
        "- FIN-WS-22 (10.20.5.77) beaconed to 185.220.101.47:443 and 45.95.147.12:8080; now isolated.\n\n"
        "Next steps: reset jsmith@acme.io credentials, block both IPs and the domain at the firewall, "
        "and search mail logs for other recipients."))
    return c.doc


def car_repair() -> dict:
    c = Chat("car-118", "Squeaky brakes on the Civic", "general-assistant", "2026-02-08T11:30:00")
    c.user("My 2016 Honda Civic makes a high pitched squeal when braking, worse in the morning. "
           "Dave at the garage in Leeds quoted £320 for front pads and discs. Can I do it myself? "
           "I have a jack, axle stands and a basic socket set.")
    c.assistant(
        think("Morning squeal can be surface rust on the discs, which goes away after a few stops. "
              "Constant squeal means wear indicators. Front pads and discs on a Civic are a doable DIY job "
              "but needs a torque wrench and brake cleaner. Mention safety."),
        say("A squeal that fades after a few stops is often just surface rust on the discs overnight. If it "
            "squeals on every stop, the pads' wear indicators are touching the discs.\n\n"
            "It's a very doable DIY job on a Civic. Besides your jack, axle stands and sockets you'll need:\n"
            "- a torque wrench (caliper bracket bolts are 108 Nm, wheel nuts 108 Nm)\n"
            "- a brake piston rewind tool or a large G-clamp\n- brake cleaner and copper grease\n"
            "- new pads and discs (about £90 for a quality set from Brembo or TRW)\n\n"
            "Total parts and tools: roughly £150 versus £320 at the garage."))
    p1 = c.call("parts_lookup", vehicle="Honda Civic 2016 1.8 i-VTEC", part="front brake discs and pads")
    c.user("Which exact parts fit my car? The reg is YX16 ABC.")
    c.assistant(p1)
    c.tool(p1["id"], "parts_lookup", "Brembo 09.A455.11 front discs (282 mm vented) £58.40 pair\n"
                                     "TRW GDB3470 front pads £31.99\nFits: Honda Civic IX (FK) 1.8 2012-2017")
    c.assistant(say("For a 2016 Civic 1.8 i-VTEC: Brembo 09.A455.11 discs (282 mm vented, £58.40) and TRW GDB3470 "
                    "pads (£31.99). Double-check the disc diameter against your current ones before fitting."))
    return c.doc


def history() -> dict:
    c = Chat("hist-02", "Ada Lovelace and the Analytical Engine", "general-assistant", "2026-01-20T19:00:00")
    c.user("Who was Ada Lovelace and why is she called the first programmer?")
    c.assistant(say(
        "Ada Lovelace (1815–1852) was an English mathematician, daughter of the poet Lord Byron. "
        "She worked with Charles Babbage on his proposed Analytical Engine.\n\n"
        "In 1843 she translated an article by the Italian engineer Luigi Menabrea and added her own Notes, "
        "three times longer than the original. Note G describes an algorithm for computing Bernoulli numbers "
        "on the Engine, which is widely considered the first published computer program.\n\n"
        "She also foresaw that such machines could manipulate symbols and even compose music, "
        "not just calculate numbers."))
    c.user("Did Babbage ever build the Analytical Engine?")
    c.assistant(
        think("No. He built part of the Difference Engine; the Science Museum in London completed "
              "Difference Engine No. 2 in 1991. The Analytical Engine only has a trial piece."),
        say("No. Babbage only built a small trial piece of the mill before his death in 1871, largely because of "
            "funding disputes with the British government. The Science Museum in London later built his "
            "Difference Engine No. 2 from the original plans in 1991, proving the design worked."))
    return c.doc


def multi_file() -> dict:
    garden = Chat("gard-1", "Tomatoes in a small garden", "general-assistant", "2026-04-01T08:00:00")
    garden.user("I have a 2x3 m raised bed in Bristol. Which tomato varieties and companion plants should I grow?")
    garden.assistant(say("For Bristol's mild but damp climate choose blight-resistant varieties like 'Crimson Crush' "
                         "and 'Mountain Magic'. Plant basil and marigolds between them to deter whitefly, and "
                         "keep potatoes far away because they share blight."))
    vet = Chat("vet-1", "Dog ate chocolate", "general-assistant", "2026-04-03T21:40:00")
    vet.user("My beagle Biscuit just ate half a bar of dark chocolate (about 50 g). He weighs 12 kg. Is it dangerous?")
    calc = vet.call("toxicity_calculator", substance="theobromine", source="dark chocolate", amount_g=50, dog_weight_kg=12)
    vet.assistant(think("Dark chocolate has ~5.5 mg/g theobromine → 275 mg / 12 kg ≈ 23 mg/kg: mild to moderate "
                        "toxicity range. Recommend calling a vet now."), calc)
    vet.tool(calc["id"], "toxicity_calculator", "Theobromine dose ≈ 23 mg/kg — mild–moderate signs expected "
                                                 "(vomiting, restlessness, tachycardia) above 20 mg/kg.")
    vet.assistant(say("Yes, that's a concerning dose (~23 mg/kg of theobromine). Call your vet or the "
                      "Animal PoisonLine now; they may induce vomiting if it was within the last 2 hours."))
    return {"format": "synthsift.example/v1", "conversations": [garden.doc, vet.doc]}


def openai_style() -> list:
    """OpenAI chat-completions log (tool_calls with JSON-string arguments)."""
    return [
        {"role": "system", "content": "You are a data assistant."},
        {"role": "user", "content": "How many orders did we ship to Germany last month? The table is sales.orders."},
        {"role": "assistant", "content": None,
         "reasoning_content": "Query the orders table filtered on country = 'DE' and last month's date range.",
         "tool_calls": [{"id": "call_q1", "type": "function", "function": {
             "name": "run_sql",
             "arguments": json.dumps({"query": "SELECT count(*) FROM sales.orders WHERE country = 'DE' AND "
                                               "shipped_at >= '2026-05-01' AND shipped_at < '2026-06-01'",
                                      "database": "warehouse"})}}]},
        {"role": "tool", "tool_call_id": "call_q1", "content": "count\n-----\n1842"},
        {"role": "assistant", "content": "You shipped 1,842 orders to Germany in May 2026."},
    ]


def jsonl_notes() -> str:
    rows = [
        {"role": "user", "content": "Summarise the key points of the Paris Agreement for my climate class."},
        {"role": "assistant", "content": [
            {"type": "thinking", "text": "Main points: 2015, COP21, limit warming well below 2°C, pursue 1.5°C, "
                                         "NDCs every five years, climate finance $100 billion."},
            {"type": "text", "text": "The Paris Agreement (adopted at COP21 in December 2015) commits nearly "
                                     "200 countries to keep global warming well below 2 °C and to pursue 1.5 °C. "
                                     "Each country submits Nationally Determined Contributions (NDCs) every five "
                                     "years, and richer nations pledged $100 billion a year in climate finance."}]},
    ]
    return "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n"


def long_refactor(turns: int = 140) -> dict:
    """A long, procedurally generated coding session."""
    rng = random.Random(42)
    c = Chat("dev-9001", "Refactor billing service to async", "coder-model-3", "2026-07-01T09:00:00",
             "You are a coding agent working in /home/dev/billing. Run the tests after every change.")
    modules = ["billing/api/routes.py", "billing/api/deps.py", "billing/db/session.py", "billing/db/models.py",
               "billing/services/invoices.py", "billing/services/payments.py", "billing/services/tax.py",
               "billing/integrations/stripe_client.py", "billing/integrations/email.py", "billing/workers/retry.py",
               "billing/config.py", "billing/utils/money.py"]
    errors = ["TypeError: object Invoice can't be used in 'await' expression",
              "RuntimeError: Event loop is closed",
              "sqlalchemy.exc.MissingGreenlet: greenlet_spawn has not been called",
              "AssertionError: assert 402 == 200",
              "KeyError: 'STRIPE_API_KEY'",
              "AttributeError: 'AsyncSession' object has no attribute 'query'"]
    people = ["Priya", "Jonas", "Mei", "Carlos"]
    c.user("Let's migrate the billing service from sync SQLAlchemy + requests to async SQLAlchemy 2.0 and httpx. "
           "Priya owns the payments code and Jonas the tax module. Keep the Stripe integration behaviour identical. "
           "The repo is at /home/dev/billing and CI runs on GitHub Actions.")
    for i in range(turns):
        mod = modules[i % len(modules)]
        phase = i % 5
        if phase == 0:
            r = c.call("read_file", path=f"/home/dev/billing/{mod}")
            c.assistant(think(f"Next module: {mod}. Look for session.query, requests.* calls and blocking I/O "
                              f"that need to become async."), r)
            body = "\n".join(
                f"def {name}(session, invoice_id):\n    return session.query(Invoice).get(invoice_id)"
                for name in rng.sample(["get_invoice", "load_payment", "fetch_customer", "compute_tax",
                                        "send_receipt", "retry_charge"], 2))
            c.tool(r["id"], "read_file", f"# {mod}\nimport requests\nfrom sqlalchemy.orm import Session\n\n{body}")
        elif phase == 1:
            e = c.call("edit_file", path=f"/home/dev/billing/{mod}",
                       old_string="session.query(Invoice).get(invoice_id)",
                       new_string="await session.get(Invoice, invoice_id)")
            c.assistant(say(f"Converting `{mod}` to `AsyncSession` and `await session.get()`."), e)
            c.tool(e["id"], "edit_file", f"Edited /home/dev/billing/{mod} (1 replacement)")
        elif phase == 2:
            t = c.call("bash", command=f"cd /home/dev/billing && uv run pytest -q tests/{mod.split('/')[-1].replace('.py', '')}_test.py")
            c.assistant(t)
            if rng.random() < 0.55:
                err = rng.choice(errors)
                c.tool(t["id"], "bash", f"F.. [100%]\n=== FAILURES ===\n{err}\n1 failed, 2 passed in 0.{rng.randint(10, 99)}s",
                       error=True)
                c.assistant(think(f"Failure: {err.split(':')[0]}. Probably a caller still uses the sync API or "
                                  f"a fixture creates a sync Session. Consider asking {rng.choice(people)}, "
                                  f"but first try pytest-asyncio's event_loop fixture."),
                            say(f"Test failed with `{err.split(':')[0]}`; fixing the fixture in tests/conftest.py."))
            else:
                c.tool(t["id"], "bash", f"... [100%]\n3 passed in 0.{rng.randint(10, 99)}s")
        elif phase == 3:
            if rng.random() < 0.3:
                c.user(rng.choice([
                    f"{rng.choice(people)} says the Stripe webhook signature check must stay synchronous. OK?",
                    "Can you make sure we don't leak connections? We saw 'too many clients' on postgres-prod-2 last week.",
                    "Remember STRIPE_API_KEY comes from Vault at secret/billing/stripe, not the env file.",
                    "How much of the codebase is done? Give me a quick status.",
                ]))
            g = c.call("grep", pattern="requests\\.(get|post)", path="/home/dev/billing", glob="*.py")
            c.assistant(g)
            hits = rng.sample(modules, 2)
            c.tool(g["id"], "grep", "\n".join(f"/home/dev/billing/{h}:{rng.randint(5, 180)}: resp = requests.post(url, json=payload, timeout=10)" for h in hits))
        else:
            c.assistant(say(
                f"Progress: {i + 1}/{turns} steps. `{mod}` now uses `httpx.AsyncClient` with a shared client "
                f"created in billing/api/deps.py and closed on shutdown. Connection pool size is 10 "
                f"(DATABASE_POOL_SIZE) with pool_pre_ping enabled for postgres-prod-2.internal:5432."))
    gh = c.call("bash", command="git push origin feat/async-billing && gh pr create --fill")
    c.assistant(say("All modules converted. Pushing the branch and opening a PR."), gh)
    c.tool(gh["id"], "bash", "https://github.com/acme/billing/pull/512")
    c.assistant(say("PR #512 is open: https://github.com/acme/billing/pull/512 – CI on GitHub Actions is running."))
    return c.doc


# --------------------------------------------------------------------------
FILES: dict[str, object] = {
    "laptop-ana/ana/example/transcript1-garlic-bread.json": garlic_bread,
    "laptop-ana/ana/example/transcript2-road-trip.json": road_trip,
    "laptop-ana/ana/openai/chat-export-001.json": openai_style,
    "buildbox-01/bob/example/transcript1-nginx-502.json": nginx_incident,
    "buildbox-01/bob/example/transcript2-k8s-cronjob.json": k8s_migration,
    "buildbox-01/bob/example/transcript3-async-refactor-long.json": long_refactor,
    "soc-ws-7/chen/example/transcript1-phishing-triage.json": phishing_triage,
    "home-mac/sam/example/transcript1-civic-brakes.json": car_repair,
    "home-mac/sam/example/transcript2-garden-and-dog.json": multi_file,
    "research-pc/lee/example/transcript1-ada-lovelace.json": history,
    "research-pc/lee/example/transcript2-paris-agreement.jsonl": jsonl_notes,
}


def main() -> None:
    out_dir = ROOT / "transcripts"
    zip_path = ROOT / "synthsift-samples.zip"
    written = []
    for rel, fn in FILES.items():
        data = fn()  # type: ignore[operator]
        text = data if isinstance(data, str) else json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        path = out_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        written.append((rel, text))
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, text in written:
            info = zipfile.ZipInfo(rel, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, text)
    print(f"wrote {len(written)} transcripts to {out_dir} and {zip_path}")


if __name__ == "__main__":
    main()
