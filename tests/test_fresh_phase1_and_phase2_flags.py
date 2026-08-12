import asyncio
import uuid
from dataclasses import replace
from pathlib import Path
from time import sleep

import httpx
from fastapi.testclient import TestClient

from contact_enrichment.llm import ContactEnrichment, _normalize_enrichment_payload
from contact_enrichment.search import SearchItem
from securedesk.api import create_app
from securedesk.config import Settings
from securedesk.enrichment.llm_service import LlmContactEnrichmentService
from securedesk.enrichment.models import ContactEnrichmentResult, ContactInput
from securedesk.phase1_verifier import CandidateVerification
from securedesk.serper import LinkedInCandidate, LinkedInLookup, SerperLinkedInFinder


class RejectingVerifier:
    configured = True

    def verify(self, contact, candidates):
        return CandidateVerification(None, 0, "No candidate validated.", "Test LLM")


async def no_delay(_seconds: float) -> None:
    return None


class UnusedPhase2Service:
    async def enrich(self, contact, **kwargs):
        return ContactEnrichmentResult(name=contact.name)


def test_processed_upload_skips_existing_validated_link_and_retries_miss() -> None:
    tmp_path = Path("outputs") / "test-artifacts" / f"history-{uuid.uuid4().hex}"
    settings = replace(
        Settings.load(),
        uploads_directory=tmp_path / "uploads",
        linkedin_cache_path=tmp_path / "linkedin-cache.db",
        serper_api_key="test-serper-key",
    )

    class Finder:
        configured = True

        def __init__(self) -> None:
            self.calls = 0
            self.attempts = []

        async def lookup_linkedin(self, contact):
            self.calls += 1
            self.attempts.append(contact.get("_search_attempt"))
            return LinkedInLookup(
                status="matched",
                url="https://www.linkedin.com/in/missing-person-new",
                confidence=93,
                note="Previous miss found on retry.",
            )

    with TestClient(
        create_app(
            settings,
            enrichment_service=UnusedPhase2Service(),
            phase1_verifier=RejectingVerifier(),
            phase1_sleep=no_delay,
        )
    ) as client:
        finder = Finder()
        client.app.state.linkedin_finder = finder
        response = client.post(
            "/api/uploads",
            files={
                "file": (
                    "processed.csv",
                    (
                        "Name,Organization,Title,Email,LinkedIn Profile URL,"
                        "LinkedIn Lookup Status,Flag,Flag Reason\n"
                        "Jane Doe,Example City,IT Director,jane@example.gov,"
                        "https://www.linkedin.com/in/jane-doe-old,Found and validated,NO,\n"
                        "Missing Person,Old Agency,Analyst,missing@example.gov,"
                        "User LinkedIn not found,User LinkedIn not found,YES,LinkedIn not found\n"
                    ).encode("utf-8"),
                    "text/csv",
                )
            },
        )
        assert response.status_code == 200
        batch = response.json()
        assert batch["resumed_found_count"] == 1
        assert batch["phase1_complete"] is False
        assert batch["statistics"]["pending"] == 1
        assert batch["statistics"]["found"] == 1

        assert client.post(
            "/api/linkedin/start", json={"batch_id": batch["batch_id"]}
        ).status_code == 202
        for _ in range(100):
            state = client.get(
                "/api/linkedin/status", params={"batch_id": batch["batch_id"]}
            ).json()
            if state["status"] != "running":
                break
            sleep(0.01)
        rows = client.get(
            "/api/contacts", params={"batch_id": batch["batch_id"]}
        ).json()["items"]
        assert finder.calls == 1
        assert rows[0]["linkedin_url"] == "https://www.linkedin.com/in/jane-doe-old"
        assert rows[1]["linkedin_url"] == "https://www.linkedin.com/in/missing-person-new"


def test_raw_file_reupload_uses_positive_history_without_new_search() -> None:
    tmp_path = Path("outputs") / "test-artifacts" / f"cache-{uuid.uuid4().hex}"
    settings = replace(
        Settings.load(),
        uploads_directory=tmp_path / "uploads",
        linkedin_cache_path=tmp_path / "linkedin-cache.db",
        serper_api_key="test-serper-key",
    )

    class Finder:
        configured = True

        def __init__(self) -> None:
            self.calls = 0
            self.attempts = []

        async def lookup_linkedin(self, contact):
            self.calls += 1
            self.attempts.append(contact.get("_search_attempt"))
            return LinkedInLookup(
                status="matched",
                url="https://www.linkedin.com/in/jane-doe-history",
                confidence=94,
                note="Validated and saved to history.",
            )

    content = "Name,Organization,Title,Email\nJane Doe,Example City,IT Director,jane@example.gov\n"
    with TestClient(
        create_app(
            settings,
            enrichment_service=UnusedPhase2Service(),
            phase1_verifier=RejectingVerifier(),
            phase1_sleep=no_delay,
        )
    ) as client:
        finder = Finder()
        client.app.state.linkedin_finder = finder

        def upload() -> dict:
            response = client.post(
                "/api/uploads",
                files={"file": ("contacts.csv", content.encode("utf-8"), "text/csv")},
            )
            assert response.status_code == 200
            return response.json()

        first = upload()
        assert client.post(
            "/api/linkedin/start", json={"batch_id": first["batch_id"]}
        ).status_code == 202
        for _ in range(100):
            state = client.get(
                "/api/linkedin/status", params={"batch_id": first["batch_id"]}
            ).json()
            if state["status"] != "running":
                break
            sleep(0.01)
        assert finder.calls == 1

        second = upload()
        assert second["cached_found_count"] == 1
        assert second["resumed_found_count"] == 1
        assert second["statistics"]["found"] == 1
        assert second["statistics"]["pending"] == 0
        assert second["phase1_complete"] is True
        assert finder.calls == 1


def test_exact_name_fallback_accepts_first_candidate_after_organization_change() -> None:
    tmp_path = Path("outputs") / "test-artifacts" / f"name-{uuid.uuid4().hex}"
    settings = replace(
        Settings.load(),
        uploads_directory=tmp_path / "uploads",
        linkedin_cache_path=tmp_path / "linkedin-cache.db",
        serper_api_key="test-serper-key",
    )
    candidate = LinkedInCandidate(
        url="https://www.linkedin.com/in/jane-doe-new-company",
        title="Jane Doe - Operations Analyst",
        snippet="Jane Doe works at New Company.",
        score=9,
        evidence=("name", "profile slug"),
    )

    class Finder:
        configured = True

        def __init__(self) -> None:
            self.calls = 0
            self.attempts = []

        async def lookup_linkedin(self, contact):
            self.calls += 1
            self.attempts.append(contact.get("_search_attempt"))
            return LinkedInLookup(
                status="unclear",
                candidates=[candidate],
                note="The uploaded organization did not match.",
            )

    with TestClient(
        create_app(
            settings,
            enrichment_service=UnusedPhase2Service(),
            phase1_verifier=RejectingVerifier(),
            phase1_sleep=no_delay,
        )
    ) as client:
        finder = Finder()
        client.app.state.linkedin_finder = finder
        response = client.post(
            "/api/uploads",
            files={
                "file": (
                    "contacts.csv",
                    b"Name,Organization,Title,Email\nJane Doe,Old Agency,Analyst,jane@example.gov\n",
                    "text/csv",
                )
            },
        )
        batch = response.json()
        assert client.post(
            "/api/linkedin/start", json={"batch_id": batch["batch_id"]}
        ).status_code == 202
        for _ in range(100):
            state = client.get(
                "/api/linkedin/status", params={"batch_id": batch["batch_id"]}
            ).json()
            if state["status"] != "running":
                break
            sleep(0.01)
        row = client.get(
            "/api/contacts", params={"batch_id": batch["batch_id"]}
        ).json()["items"][0]
        assert finder.calls == 2
        assert finder.attempts == [1, 2]
        assert row["lookup_status"] == "found"
        assert row["linkedin_url"] == candidate.url
        assert "First/Last or Last/First order" in row["validation_note"]
        assert "organization may be outdated" in row["validation_note"]


def test_second_search_tries_reversed_name_order_with_only_script_queries() -> None:
    requests: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = __import__("json").loads(request.content)
        requests.append(body)
        organic = (
            [
                {
                    "title": "Jane Doe | LinkedIn",
                    "link": "https://www.linkedin.com/in/jane-doe-new-company",
                    "snippet": "Jane Doe works for a new organization.",
                }
            ]
            if body["q"] == "Doe Jane LinkedIn"
            else []
        )
        return httpx.Response(200, json={"organic": organic})

    settings = replace(Settings.load(), serper_api_key="test-serper-key")
    finder = SerperLinkedInFinder(settings, transport=httpx.MockTransport(handler))
    lookup = asyncio.run(
        finder.lookup_linkedin(
            {
                "name": "Jane Doe",
                "organization": "Old Agency",
                "title": "Analyst",
                "email": "jane@old.example",
                "_search_attempt": 2,
            }
        )
    )
    assert lookup.url == "https://www.linkedin.com/in/jane-doe-new-company"
    assert requests[0]["q"] == 'site:linkedin.com/in "Doe Jane" "Old Agency"'
    assert all(item["num"] == 10 for item in requests)
    assert all(set(item) == {"q", "num"} for item in requests)
    assert [item["q"] for item in requests] == [
        'site:linkedin.com/in "Doe Jane" "Old Agency"',
        "Doe Jane LinkedIn",
    ]


def test_primary_search_uses_user_provided_query_order() -> None:
    requests: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = __import__("json").loads(request.content)
        requests.append(body)
        if body["q"] == '"jane@old.example"':
            return httpx.Response(
                200,
                json={
                    "organic": [
                        {
                            "title": "Jane Doe | LinkedIn",
                            "link": "https://www.linkedin.com/in/jane-doe",
                            "snippet": "Jane Doe can be reached at jane@old.example.",
                        }
                    ]
                },
            )
        return httpx.Response(200, json={"organic": []})

    settings = replace(Settings.load(), serper_api_key="test-serper-key")
    finder = SerperLinkedInFinder(settings, transport=httpx.MockTransport(handler))
    lookup = asyncio.run(
        finder.lookup_linkedin(
            {
                "name": "Jane Doe",
                "organization": "Old Agency",
                "email": "jane@old.example",
                "_search_attempt": 1,
            }
        )
    )

    assert [item["q"] for item in requests] == [
        'site:linkedin.com/in "Jane Doe" "Old Agency"',
        "Jane Doe LinkedIn",
        '"jane@old.example"',
    ]
    assert lookup.url == "https://www.linkedin.com/in/jane-doe"


def test_unsupported_script_query_is_skipped_without_losing_contact() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                400,
                json={"message": "Query pattern not allowed for free accounts."},
            )
        return httpx.Response(
            200,
            json={
                "organic": [
                    {
                        "title": "Jane Doe | LinkedIn",
                        "link": "https://www.linkedin.com/in/jane-doe",
                        "snippet": "Jane Doe works for a new organization.",
                    }
                ]
            },
        )

    settings = replace(Settings.load(), serper_api_key="test-serper-key")
    finder = SerperLinkedInFinder(settings, transport=httpx.MockTransport(handler))
    lookup = asyncio.run(
        finder.lookup_linkedin(
            {
                "name": "Jane Doe",
                "organization": "Old Agency",
                "_search_attempt": 2,
            }
        )
    )
    assert calls == 2
    assert lookup.status == "matched"
    assert lookup.candidates[0].url == "https://www.linkedin.com/in/jane-doe"


def test_exact_name_matching_tolerates_punctuation_but_not_spelling_change() -> None:
    from securedesk.phase1_verifier import first_exact_name_candidate

    punctuation_variant = LinkedInCandidate(
        "https://www.linkedin.com/in/arturo-j-morales",
        "Arturo J Morales - Chief Executive Officer",
        "Arturo J. Morales leads an organization.",
        9,
        ("name", "profile slug"),
    )
    misspelled = LinkedInCandidate(
        "https://www.linkedin.com/in/arturo-j-moralez",
        "Arturo J Moralez - Chief Executive Officer",
        "Arturo J Moralez leads an organization.",
        9,
        ("name", "profile slug"),
    )
    accepted = first_exact_name_candidate(
        {"name": "Arturo J. Morales"}, [punctuation_variant]
    )
    rejected = first_exact_name_candidate(
        {"name": "Arturo J. Morales"}, [misspelled]
    )
    assert accepted.candidate == punctuation_variant
    assert rejected.candidate is None


def test_exact_name_matching_allows_extra_middle_initial_but_preserves_tokens() -> None:
    from securedesk.phase1_verifier import first_exact_name_candidate

    extra_initial = LinkedInCandidate(
        "https://www.linkedin.com/in/ashley-m-stonebrink",
        "Ashley M. Stonebrink | LinkedIn",
        "Senior records professional.",
        9,
        ("name", "profile slug"),
    )
    wrong_last_name = LinkedInCandidate(
        "https://www.linkedin.com/in/ashley-m-stonebrook",
        "Ashley M. Stonebrook | LinkedIn",
        "Senior records professional.",
        9,
        ("name", "profile slug"),
    )

    accepted = first_exact_name_candidate(
        {"name": "Ashley Stonebrink"}, [extra_initial]
    )
    rejected = first_exact_name_candidate(
        {"name": "Ashley Stonebrink"}, [wrong_last_name]
    )
    assert accepted.candidate == extra_initial
    assert rejected.candidate is None


class FakeSerper:
    def _items(self, source: str) -> list[SearchItem]:
        return [
            SearchItem(
                title=f"Jane Doe {source}",
                snippet="Jane Doe is the IT Director for Example City.",
                link=f"https://evidence.example/{source}",
                source=source,
            )
        ]

    def search_general(self, name, organization):
        return self._items("web")

    def search_local_news(self, city, title, organization):
        return self._items("news")

    def search_linkedin(self, name, organization):
        return self._items("linkedin")


class ClassifiedLlm:
    def enrich(self, contact, sources, *, search_failed=False):
        return ContactEnrichment(
            role_title="IT Director",
            role_background="Jane Doe leads technology services for Example City.",
            city="Example City",
            local_context="",
            department_bucket="IT",
            sub_function="Change Management",
            needs_review=False,
            flag_reason="",
        )


def test_phase_two_flags_missing_linkedin() -> None:
    result = asyncio.run(
        LlmContactEnrichmentService(FakeSerper(), ClassifiedLlm()).enrich(
            ContactInput(
                name="Jane Doe",
                organization="Example City",
                existing_job_title="IT Director",
            )
        )
    )
    assert result.department_bucket == "IT"
    assert result.sub_function == "Change Management"
    assert result.manual_review is True
    assert "LinkedIn profile is missing or invalid" in (result.review_reason or "")


def test_phase_two_flags_missing_category_and_sub_function() -> None:
    class UnclassifiedLlm:
        def enrich(self, contact, sources, *, search_failed=False):
            return ContactEnrichment.flagged("The person's work could not be verified.")

    result = asyncio.run(
        LlmContactEnrichmentService(FakeSerper(), UnclassifiedLlm()).enrich(
            ContactInput(
                name="Jane Doe",
                organization="Example City",
                linkedin_url="https://www.linkedin.com/in/jane-doe",
            )
        )
    )
    assert result.manual_review is True
    assert "Department category is missing or invalid" in (result.review_reason or "")
    assert "Sub-function is missing or invalid" in (result.review_reason or "")


def test_title_evidence_repairs_missing_llm_category_and_sub_function() -> None:
    normalized = _normalize_enrichment_payload(
        {
            "role_background": "Jane Doe manages municipal application systems and technology changes.",
            "department_bucket": "",
            "sub_function": "",
            "needs_review": True,
            "flag_reason": "The department and sub-function were not explicitly stated.",
        },
        contact={"title": "Systems and Applications Analyst"},
    )
    assert normalized["department_bucket"] == "IT"
    assert normalized["sub_function"] == "Change Management"
    assert normalized["needs_review"] is False
    assert normalized["flag_reason"] == ""
