"""
Mock webpage fetcher for local testing.

In production, gl.get_webpage() actually fetches ClinicalTrials.gov,
PubMed, journal pages, etc. from inside GenLayer's sandboxed browser.
This sandbox has no network access to those domains, so this module
returns realistic canned fixture text keyed by URL, instead.

Two scenarios are modeled here, both drawn from the spec's own framing:

1. THERANOS-001: an outcome-switching fraud case (loosely modeled
   on the public Theranos narrative referenced in the spec) -- the
   published paper reports on a completely different, narrower endpoint
   than what was pre-registered, with an unexplained drop in sample size.

2. CARDIO-204: a LEGITIMATE early-stopping case -- a trial halted
   early on a documented, pre-specified DSMB efficacy boundary. This is
   the counter-example used to prove the system does NOT flag legitimate
   amendments as fraud.

BUG FOUND AND FIXED: an earlier version of this file also defined
"...?current=true" fixture variants, meant to represent the registry
having been silently amended between registration time and submission
time. They were dead code -- `submit_results()` in medichain_contract.py
re-fetches the exact same `registry_url` string that was stored at
registration (which is correct, real-world GenLayer behavior: the same
live URL fetched at two different points in time can legitimately return
different content because the page itself changed). But this mock
fetcher is a **pure function of the URL string**, so fetching that exact
same URL string twice always returns the exact same fixture text --
those "?current=true" entries were never actually reachable by any code
path, which meant the "undisclosed protocol amendment" fraud signal was
never actually exercised end-to-end by the test suite, despite fixtures
implying it was.

Why not just make this fetcher stateful (e.g. return the amended fixture
on the second visit to a URL)? Because this fetcher's state would then
leak across unrelated tests that happen to reuse the same registry URL
(several edge-case tests intentionally reuse THERANOS-001/CARDIO-204's
URLs to avoid needing dedicated fixtures) -- the first test to touch a
URL would poison every later test's "registration-time" snapshot for
that same URL with the "already amended" content. That's a worse bug
than the one it would fix. A safe version would need to key state by
something more specific than "URL string" (e.g. a per-trial identity
threaded through the fetch call), which isn't part of the
Callable[[str], str] interface this class depends on, and changing that
interface would make this file diverge from the real
`gl.get_webpage(url, mode="text")` signature it's meant to stand in for.

Net effect: the outcome-switching and sample-size-discrepancy signals
ARE genuinely tested end-to-end (they come from comparing the paper
against the original protocol snapshot, which this fetcher handles
correctly). The "undisclosed amendment via registry drift" signal
specifically is only exercised at the prompt-engineering level (the
prompt asks the LLM to check for it) -- not at the "the mock registry
fetch actually changed between two calls" level. If you need to verify
that specific path, the cleanest way is to test it directly against a
live GenLayer deployment, where gl.get_webpage() naturally reflects
whatever the real ClinicalTrials.gov page says at each call.
"""

FIXTURES = {
    # ---------------- Theranos-style outcome-switching scenario ----------------
    "https://clinicaltrials.gov/study/THERANOS-001": """
ClinicalTrials.gov Registration -- Study THERANOS-001
Title: Comparative Diagnostic Accuracy of the Edison Finger-Stick Analyzer
Status: Recruiting
Primary Endpoint: Diagnostic concordance between the Edison analyzer and
CLIA-certified reference laboratory methods across a panel of 15 analytes,
measured in n=200 paired venous-draw and finger-stick samples.
Hypothesis: The Edison device achieves non-inferior accuracy to standard
laboratory methods within a pre-specified 5% margin.
Planned Enrollment: 200 participants.
Amendments: none on file at registration.
""",
    "https://journal.example.org/theranos-outcomes-2016": """
Published Paper -- Journal of Clinical Innovation, 2016
Title: Patient Experience with Finger-Stick Blood Collection: A Satisfaction Survey
Abstract: This study reports on a survey of patient and technician
satisfaction with finger-stick collection using a novel analyzer (n=55).
Diagnostic concordance data for the 15-analyte reference panel described
in the original registration was not included in this analysis. No
explanation is given for the reduced sample size or the change in
reported outcome.
""",

    # ---------------- Legitimate DSMB early-stopping scenario ----------------
    "https://clinicaltrials.gov/study/CARDIO-204": """
ClinicalTrials.gov Registration -- Study CARDIO-204
Title: Long-Term Outcomes of Drug X in High-Risk Cardiovascular Patients
Status: Recruiting
Primary Endpoint: Overall survival at 24 months.
Hypothesis: Drug X reduces 24-month all-cause mortality versus placebo.
Planned Enrollment: 2000 participants.
DSMB Charter (on file at registration, dated at registration): a
pre-specified interim analysis will occur after 40% enrollment; early
stopping is permitted if the hazard ratio crosses the O'Brien-Fleming
efficacy boundary. This charter is on file BEFORE any results are
submitted, so a later early stop consistent with it is expected, not
suspicious.
""",
    "https://journal.example.org/cardio-204-results-2025": """
Published Paper -- New England Journal of Cardiology, 2025
Title: Early Termination of the CARDIO-204 Trial for Overwhelming Efficacy
Abstract: Enrollment was closed at n=810 (of a planned 2000) following a
DSMB recommendation for early stopping due to overwhelming efficacy,
consistent with the pre-specified O'Brien-Fleming stopping boundary
documented in the original protocol and DSMB charter at registration.
The primary endpoint (overall survival at 24 months) is unchanged from
registration.
""",
}

DEFAULT_FIXTURE = """
(No canned fixture available for this URL in the local mock fetcher.
In production this would be a live gl.get_webpage() fetch. Returning a
neutral placeholder so integrity analysis can still run.)
"""


def mock_webpage_fetcher(url: str) -> str:
    """Callable[[str], str] matching the MediChainContract fetcher interface."""
    return FIXTURES.get(url, DEFAULT_FIXTURE).strip()
