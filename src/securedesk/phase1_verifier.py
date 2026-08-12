from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Callable

from openai import OpenAI
from pydantic import BaseModel, Field, model_validator

from contact_enrichment.cache import ApiCache
from contact_enrichment.config import PipelineSettings
from securedesk.config import Settings
from securedesk.serper import LinkedInCandidate


class CandidateDecision(BaseModel):
    is_match: bool
    candidate_number: int | None = Field(default=None, ge=1, le=3)
    contact_name: str = Field(default="", max_length=300)
    confidence: int = Field(default=0, ge=0, le=100)
    reason: str = Field(default="", max_length=800)

    @model_validator(mode="after")
    def validate_selection(self) -> "CandidateDecision":
        if self.is_match and self.candidate_number is None:
            raise ValueError("candidate_number is required when is_match is true")
        if not self.is_match and self.candidate_number is not None:
            raise ValueError("candidate_number must be null when is_match is false")
        return self


@dataclass(frozen=True)
class CandidateVerification:
    candidate: LinkedInCandidate | None
    confidence: int
    note: str
    provider: str | None = None


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    base_url: str
    api_key: str
    models: tuple[str, ...]


class Phase1CandidateVerifier:
    """Use configured LLM providers only after deterministic matching is exhausted."""

    def __init__(
        self,
        settings: Settings,
        *,
        cache: ApiCache | None = None,
        client_factory: Callable[..., Any] = OpenAI,
    ) -> None:
        pipeline = PipelineSettings.load(settings.root)
        self.cache = cache or ApiCache(pipeline.cache_directory / "api_cache.sqlite3")
        self.client_factory = client_factory
        self.providers: list[ProviderSpec] = []
        if settings.groq_api_key:
            self.providers.append(
                ProviderSpec(
                    "Groq",
                    "https://api.groq.com/openai/v1",
                    settings.groq_api_key,
                    pipeline.all_groq_models,
                )
            )
        if settings.deepseek_api_key:
            self.providers.append(
                ProviderSpec(
                    "DeepSeek",
                    "https://api.deepseek.com",
                    settings.deepseek_api_key,
                    pipeline.all_deepseek_models,
                )
            )
        if settings.openrouter_api_key:
            self.providers.append(
                ProviderSpec(
                    "OpenRouter",
                    "https://openrouter.ai/api/v1",
                    settings.openrouter_api_key,
                    pipeline.all_models,
                )
            )

    @property
    def configured(self) -> bool:
        return bool(self.providers)

    @property
    def provider_name(self) -> str:
        return " → ".join(provider.name for provider in self.providers)

    def verify(
        self,
        contact: dict[str, Any],
        candidates: list[LinkedInCandidate],
    ) -> CandidateVerification:
        candidates = candidates[:3]
        if not candidates:
            return CandidateVerification(None, 0, "No LinkedIn candidates were available.")
        if not self.providers:
            return CandidateVerification(
                None,
                0,
                "No LLM provider is configured for candidate verification.",
            )

        payload = _prompt_payload(contact, candidates)
        cache_key = hashlib.sha256(
            json.dumps(
                {
                    "schema_version": 2,
                    "providers": [
                        {"name": provider.name, "models": provider.models}
                        for provider in self.providers
                    ],
                    "payload": payload,
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        cached = self.cache.get("phase1_candidate_verification", cache_key)
        if isinstance(cached, dict):
            return self._validated_result(
                CandidateDecision.model_validate(cached.get("decision") or {}),
                contact,
                candidates,
                str(cached.get("provider") or "Cached LLM"),
            )

        failures: list[str] = []
        for provider in self.providers:
            client = self.client_factory(
                base_url=provider.base_url,
                api_key=provider.api_key,
                timeout=60.0,
                max_retries=0,
            )
            for model in provider.models:
                try:
                    response = client.chat.completions.create(
                        model=model,
                        messages=[
                            {"role": "system", "content": _system_prompt()},
                            {
                                "role": "user",
                                "content": json.dumps(
                                    payload,
                                    ensure_ascii=False,
                                    indent=2,
                                ),
                            },
                        ],
                        response_format={"type": "json_object"},
                        temperature=0,
                        max_tokens=700,
                    )
                    decision = CandidateDecision.model_validate(
                        _message_json(response.choices[0].message)
                    )
                    self.cache.set(
                        "phase1_candidate_verification",
                        cache_key,
                        {
                            "provider": provider.name,
                            "decision": decision.model_dump(mode="json"),
                        },
                    )
                    return self._validated_result(
                        decision,
                        contact,
                        candidates,
                        provider.name,
                    )
                except Exception as error:
                    failures.append(
                        f"{provider.name}/{model}: {' '.join(str(error).split())[:180]}"
                    )

        return CandidateVerification(
            None,
            0,
            "All configured LLM candidate validators failed. " + "; ".join(failures[:3]),
        )

    @staticmethod
    def _validated_result(
        decision: CandidateDecision,
        contact: dict[str, Any],
        candidates: list[LinkedInCandidate],
        provider: str,
    ) -> CandidateVerification:
        uploaded_name = _clean_name(contact.get("name"))
        if not decision.is_match:
            return CandidateVerification(
                None,
                0,
                decision.reason or "The LLM rejected all three candidates.",
                provider,
            )
        if _clean_name(decision.contact_name).casefold() != uploaded_name.casefold():
            return CandidateVerification(
                None,
                0,
                "The LLM-selected name did not exactly match the uploaded name.",
                provider,
            )
        index = int(decision.candidate_number or 0) - 1
        if index < 0 or index >= len(candidates):
            return CandidateVerification(None, 0, "The LLM selected an invalid candidate.", provider)
        candidate = candidates[index]
        if not _candidate_has_exact_name(candidate, uploaded_name):
            return CandidateVerification(
                None,
                0,
                "The selected profile evidence did not contain the exact uploaded name.",
                provider,
            )
        supporting = set(candidate.evidence) & {
            "organization",
            "title",
            "email domain",
            "location",
        }
        if not supporting:
            confidence = min(72, max(60, decision.confidence))
            note = (
                f"LLM validated candidate {index + 1} by the exact uploaded name "
                f"({provider}); the uploaded organization may be outdated."
            )
        else:
            confidence = min(90, max(60, decision.confidence))
            note = (
                f"LLM validated candidate {index + 1} using exact uploaded name and "
                f"{', '.join(sorted(supporting))} evidence ({provider})."
            )
        return CandidateVerification(candidate, confidence, note, provider)


def _clean_name(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _candidate_has_exact_name(candidate: LinkedInCandidate, uploaded_name: str) -> bool:
    uploaded_name = _clean_name(uploaded_name)
    name_orders = _name_token_orders(uploaded_name)
    if not name_orders:
        return False
    return any(
        _contains_ordered_name(field, name_tokens)
        for field in (candidate.title, candidate.snippet)
        for name_tokens in name_orders
    )


def _ordered_name_tokens(value: Any) -> list[str]:
    return re.findall(r"[^\W_]+", str(value or "").casefold(), flags=re.UNICODE)


def _name_token_orders(value: Any) -> list[list[str]]:
    tokens = _ordered_name_tokens(value)
    if not tokens:
        return []
    orders = [tokens]
    if len(tokens) >= 2:
        orders.append(list(reversed(tokens)))
        if len(tokens) > 2:
            orders.append([tokens[-1], *tokens[:-1]])
    unique: list[list[str]] = []
    for order in orders:
        if order not in unique:
            unique.append(order)
    return unique


def _contains_ordered_name(field: Any, name_tokens: list[str]) -> bool:
    evidence_tokens = _ordered_name_tokens(field)
    if not evidence_tokens or len(evidence_tokens) < len(name_tokens):
        return False
    expected_index = 0
    previous_match = -1
    for index, token in enumerate(evidence_tokens):
        if token != name_tokens[expected_index]:
            continue
        if previous_match >= 0 and index - previous_match > 3:
            expected_index = 0
            previous_match = -1
            if token != name_tokens[0]:
                continue
        previous_match = index
        expected_index += 1
        if expected_index == len(name_tokens):
            return True
    return False


def first_exact_name_candidate(
    contact: dict[str, Any],
    candidates: list[LinkedInCandidate],
) -> CandidateVerification:
    """Pick the first candidate with the uploaded name in either common order."""

    uploaded_name = _clean_name(contact.get("name"))
    for candidate in candidates:
        if not _candidate_has_exact_name(candidate, uploaded_name):
            continue
        supporting = set(candidate.evidence) & {
            "organization",
            "title",
            "email domain",
            "location",
        }
        confidence = 72 if supporting else 62
        detail = (
            f" with {', '.join(sorted(supporting))} evidence"
            if supporting
            else ""
        )
        return CandidateVerification(
            candidate,
            confidence,
            (
                f"Selected the first LinkedIn result containing every uploaded name "
                f"token in First/Last or Last/First order{detail}; the uploaded "
                f"organization may be outdated."
            ),
            "Exact-name fallback",
        )
    return CandidateVerification(
        None,
        0,
        "No candidate contained the exact uploaded full-name spelling.",
        "Exact-name fallback",
    )


def _prompt_payload(
    contact: dict[str, Any],
    candidates: list[LinkedInCandidate],
) -> dict[str, Any]:
    return {
        "uploaded_contact": {
            "name": contact.get("name") or "",
            "organization": contact.get("organization") or "",
            "title": contact.get("title") or "",
            "email": contact.get("email") or "",
            "location": contact.get("location") or "",
        },
        "candidates": [
            {
                "candidate_number": index,
                "url": candidate.url,
                "title": candidate.title,
                "snippet": candidate.snippet,
                "deterministic_evidence": list(candidate.evidence),
            }
            for index, candidate in enumerate(candidates, start=1)
        ],
    }


def _system_prompt() -> str:
    return (
        "You verify whether one of up to three public LinkedIn search-result candidates "
        "belongs to an uploaded contact. Never guess. Select a candidate only when the "
        "candidate contains every uploaded name token with exact spelling in either "
        "First/Last or Last/First order; extra middle initials are allowed. Prefer organization, "
        "job-title, email-domain, or location support, but do not reject an otherwise exact "
        "full-name candidate only because the organization changed. Prefer the first exact-name "
        "candidate when no stronger supported candidate exists. "
        "Return one JSON object with is_match, candidate_number (1-3 or null), "
        "contact_name (copy the uploaded name exactly when matched), confidence (0-100), "
        "and a short reason. Reject ambiguous candidates."
    )


def _message_json(message: Any) -> dict[str, Any]:
    parsed = getattr(message, "parsed", None)
    if isinstance(parsed, BaseModel):
        return parsed.model_dump(mode="json")
    if isinstance(parsed, dict):
        return parsed
    content = getattr(message, "content", "")
    if isinstance(content, list):
        content = "".join(
            str(item.get("text") or "") if isinstance(item, dict) else str(item)
            for item in content
        )
    text = str(content or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("The LLM did not return a JSON object")
    payload = json.loads(text[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("The LLM response was not a JSON object")
    return payload
