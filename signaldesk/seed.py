"""The demo book of business.

Every record here is a real-looking raw signal: a call, an email, a ticket, a
QBR note. The dataset is deliberately *outcome-bearing* — some accounts were
saved, some were lost, and the save plays are recorded. That closed loop is what
lets the agent learn which play works instead of guessing: the precedent it cites
in a save plan is a real account from this file.

Target demo account: Meridian Freight (at-risk, ARR $148k, mid-market).
"""

from __future__ import annotations

from datetime import datetime, timedelta

# Anchor the corpus so "N days ago" always resolves sensibly.
TODAY = datetime(2026, 9, 29)

S = lambda d: (TODAY - timedelta(days=d)).strftime("%Y-%m-%d")  # noqa: E731

DATASETS = ("demo", "blank")

HISTORY: list[dict] = [
    # ---------------- SAVED accounts: the precedent set ----------------
    {
        "account": "Halcyon Logistics",
        "text": (
            f"[{S(420)}] Call — Halcyon Logistics ($92k ARR, mid-market, logistics). "
            "Champion Marcus Webb (VP Ops) opened with: 'The carrier tracking API has missed "
            "three committed delivery dates this quarter and my dispatchers are manually "
            "re-keying manifests. I've escalated to my COO.' Marcus asked for a 15% discount "
            "and a written delivery commitment. I flagged the delay pattern to our VP Product."
        ),
        "channel": "call",
    },
    {
        "account": "Halcyon Logistics",
        "text": (
            f"[{S(395)}] Email — to Marcus Webb. Offered a 10% one-time service credit (not a "
            "recurring discount) contingent on a 30-day reliability remediation plan. Marcus "
            "replied that a recurring discount matters less to him than the dates being real."
        ),
        "channel": "email",
    },
    {
        "account": "Halcyon Logistics",
        "text": (
            f"[{S(360)}] Call — Exec sponsor meeting. I brought our VP Engineering onto the call "
            "with Marcus AND his COO Priya Chandrasekhar. We committed to a named owner and "
            "hard dates for the tracking API fixes. Marcus said: 'This is the first time I've "
            "seen the people who wrote the code.' Priya asked for weekly written status."
        ),
        "channel": "call",
    },
    {
        "account": "Halcyon Logistics",
        "text": (
            f"[{S(300)}] QBR — Halcyon Logistics. Three of four remediation milestones met on time. "
            "Marcus confirmed renewal for 2 years at $101k. OUTCOME: SAVED via exec-sponsor "
            "alignment and dated commitments. Marcus noted the recurring discount request "
            "was dropped once his COO felt heard."
        ),
        "channel": "qbr",
    },
    {
        "account": "Ironvale Manufacturing",
        "text": (
            f"[{S(450)}] Call — Ironvale Manufacturing ($64k ARR, industrial). Champion Elena "
            "Vasquez (Director of IT) reported the ERP sync had failed twice during month-end "
            "close. Her CFO joined for the last 10 minutes and said the failures 'create real "
            "audit risk'. Elena asked whether we had a named engineering contact."
        ),
        "channel": "call",
    },
    {
        "account": "Ironvale Manufacturing",
        "text": (
            f"[{S(410)}] Email — Proposed a named engineering contact plus a written escalation "
            "path. Elena forwarded it to her CFO. CFO replied approving renewal but asked for "
            "the escalation path be contractual."
        ),
        "channel": "email",
    },
    {
        "account": "Ironvale Manufacturing",
        "text": (
            f"[{S(380)}] Call — Set up the contractual escalation SLA with the CFO present. "
            "Elena's CFO said our competitor 'cannot commit to response times in writing'. "
            "OUTCOME: SAVED — renewed at $64k. The decisive factor was the contractual "
            "escalation SLA, not a discount."
        ),
        "channel": "call",
    },
    {
        "account": "Bluepeak Analytics",
        "text": (
            f"[{S(500)}] Call — Bluepeak Analytics ($210k ARR, enterprise, fintech data). "
            "Champion Tomás Oliveira (Head of Data) was upbeat; the team had shipped three "
            "features they asked for. He mentioned budget was being cut 12% next quarter."
        ),
        "channel": "call",
    },
    {
        "account": "Bluepeak Analytics",
        "text": (
            f"[{S(340)}] Email — Budget cut became real: Tomás said his director told him to "
            "cut or 'de-risk' one vendor. He asked what usage we could cut to hit a lower tier."
        ),
        "channel": "email",
    },
    {
        "account": "Bluepeak Analytics",
        "text": (
            f"[{S(330)}] Call — Ran a usage review showing 62% of seats used daily and a workload "
            "his team couldn't get elsewhere. We built a right-sized tier at $178k instead of "
            "$210k. Tomás told his director the tool was load-bearing. "
            "OUTCOME: SAVED at a reduced price via usage proof + right-sizing."
        ),
        "channel": "call",
    },
    # ---------------- LOST accounts: the counter-evidence ----------------
    {
        "account": "Cobalt Ridge Energy",
        "text": (
            f"[{S(300)}] Call — Cobalt Ridge Energy ($48k ARR). Champion Sam Whitfield (IT Lead) "
            "reported slow reports. He asked for a 20% discount to stay."
        ),
        "channel": "call",
    },
    {
        "account": "Cobalt Ridge Energy",
        "text": (
            f"[{S(280)}] Email — Approved an 18% discount and a $6k credit. Sam replied: "
            "'Thanks, but honestly we're probably going to look at the two other vendors "
            "you mentioned last year.' No further meetings scheduled."
        ),
        "channel": "email",
    },
    {
        "account": "Cobalt Ridge Energy",
        "text": (
            f"[{S(240)}] Churn — Cobalt Ridge Energy cancelled. Post-mortem: we responded to the "
            "discount request with a discount. Sam's real issue was that his new CTO — not Sam — "
            "was the economic buyer and had no relationship with us. We never reached him. "
            "OUTCOME: LOST. Lesson: discounting a champion who is not the economic buyer buys "
            "time, not the account."
        ),
        "channel": "note",
    },
    {
        "account": "Vantage Health Supply",
        "text": (
            f"[{S(260)}] Call — Vantage Health Supply ($75k ARR). Champion Dr. Amy Liang (Clinical "
            "Ops) said onboarding had 'taken forever' — 5 weeks late on go-live. She asked for a "
            "refund of the onboarding fees."
        ),
        "channel": "call",
    },
    {
        "account": "Vantage Health Supply",
        "text": (
            f"[{S(240)}] Email — Issued a $9k onboarding fee refund plus a dedicated onboarding "
            "CSM. Amy acknowledged it but said her executive sponsor had 'already interviewed "
            "two alternatives'."
        ),
        "channel": "email",
    },
    {
        "account": "Vantage Health Supply",
        "text": (
            f"[{S(200)}] Churn — Vantage Health Supply cancelled at renewal. Post-mortem: by the "
            "time we responded to the go-live failure, Amy's exec sponsor had already run a "
            "competitive bake-off. The refund arrived ~6 weeks after the first complaint. "
            "OUTCOME: LOST. Lesson: a service recovery that lands after the sponsor leaves is "
            "a refund, not a save."
        ),
        "channel": "note",
    },
    {
        "account": "Pinnacle Freight Group",
        "text": (
            f"[{S(210)}] Call — Pinnacle Freight Group ($118k ARR). Champion Ray Okafor (Director "
            "of IT) said our API rate limits 'broke their dispatcher app' during peak. He wanted "
            "higher limits and threatened to move."
        ),
        "channel": "call",
    },
    {
        "account": "Pinnacle Freight Group",
        "text": (
            f"[{S(195)}] Email — Sent a rate-limit increase and a capacity plan. Ray said the "
            "increase would help but that his new CTO was 're-platforming everything' and had "
            "standardised on a competitor. He asked for a migration export."
        ),
        "channel": "email",
    },
    {
        "account": "Pinnacle Freight Group",
        "text": (
            f"[{S(160)}] Churn — Pinnacle Freight Group migrated to a competitor's freight suite. "
            "Post-mortem: technical fix was correct and delivered, but the account was already "
            "in a re-platforming cycle we didn't know about. OUTCOME: LOST. Lesson: solving the "
            "ticket does not save an account that a new technical buyer has already decided on."
        ),
        "channel": "note",
    },
    # ---------------- Target account: the live risk ----------------
    {
        "account": "Meridian Freight",
        "text": (
            f"[{S(190)}] QBR — Meridian Freight ($148k ARR, mid-market, freight brokerage). "
            "Champion Devon Achebe (VP Operations) is our strongest advocate. He said the "
            "load-matching engine is 'the only reason we didn't build this in-house'. "
            "Economic buyer: CFO Alison Kerr, whom Devon has never brought onto a call. "
            "Devon's stated goal: prove ROI to Alison before budget season."
        ),
        "channel": "qbr",
    },
    {
        "account": "Meridian Freight",
        "text": (
            f"[{S(150)}] Email — Devon forwarded the ROI deck and asked for a one-page summary he "
            "could paste into Alison's budget memo. Note: in the deck, the payback period claim "
            "was 11 months; our finance team later corrected it to 14 months."
        ),
        "channel": "email",
    },
    {
        "account": "Meridian Freight",
        "text": (
            f"[{S(96)}] Ticket — SUPPORT-4471. Load-matching API returned 502s for roughly 40 "
            "minutes on a Tuesday morning; 18 dispatchers were blocked. Root cause was a bad "
            "config push. Devon: 'We lost a morning of dispatch. This is the third incident "
            "this quarter.' He asked how many more of these are coming."
        ),
        "channel": "ticket",
    },
    {
        "account": "Meridian Freight",
        "text": (
            f"[{S(70)}] Call — Post-incident review. Devon was measured, not angry: he said he "
            "understands incidents happen, but he has to defend our reliability to Alison and "
            "has nothing to show her. He asked directly: 'Can you get me in front of Alison?' "
            "He also noted a competitor, Freightwise, has been 'in the building twice' for "
            "a trade-association event."
        ),
        "channel": "call",
    },
    {
        "account": "Meridian Freight",
        "text": (
            f"[{S(41)}] Email — I asked Devon for a 20-minute intro to Alison and offered to "
            "prepare a reliability review deck with the SRE lead. Devon replied that Alison is "
            "'not taking intro calls from vendors right now' but would look at a written summary. "
            "He also said Freightwise quoted him roughly 15% below our renewal price."
        ),
        "channel": "email",
    },
    {
        "account": "Meridian Freight",
        "text": (
            f"[{S(9)}] Slack — Devon posted in the shared channel: 'Heads up that budget review "
            "is moving up. Looks like we decide by Oct 15.' No other message in 9 days."
        ),
        "channel": "slack",
    },
    # ---------------- A second live risk, for board() ----------------
    {
        "account": "Solstice Media",
        "text": (
            f"[{S(120)}] QBR — Solstice Media ($57k ARR). Champion Carla Mendes (Head of Growth) "
            "loved the product. Economic buyer: her VP Marketing, Neil Boyd, who has never "
            "joined a call."
        ),
        "channel": "qbr",
    },
    {
        "account": "Solstice Media",
        "text": (
            f"[{S(30)}] Call — Carla mentioned budget is 'under review' and that Neil is asking "
            "hard questions about attribution. She asked what data would help her make the case "
            "internally. She sounded fine, but the budget language is new."
        ),
        "channel": "call",
    },
]


def build_dataset(name: str = "demo") -> list[dict]:
    """Return seed records shaped for ``retain_batch``."""
    if name == "blank":
        return []
    return list(HISTORY)


def target_account() -> str:
    return "Meridian Freight"
