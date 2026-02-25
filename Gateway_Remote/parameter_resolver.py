from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence, Tuple

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_EQ_BAND_RE = re.compile(r"^(?:band)?([1-8])(?:filter)?(type|frequency|freq|gain|q)$")


@dataclass(frozen=True)
class ResolutionTrace:
    matched_by: str | None
    query: str
    normalized_query: str
    candidate_chain: List[str]
    resolved_param_name: str | None


def normalize_query(text: Any) -> str:
    return "".join(_TOKEN_RE.findall(str(text or "").lower()))


def build_parameter_index(parameters: Sequence[Any]) -> Dict[str, Any]:
    index: Dict[str, Any] = {}
    for param in parameters:
        key = normalize_query(getattr(param, "name", ""))
        if key and key not in index:
            index[key] = param
    return index


def eq_band_rule_candidates(normalized_query: str) -> List[str]:
    if not normalized_query:
        return []
    match = _EQ_BAND_RE.match(normalized_query)
    if not match:
        return []

    band, field = match.groups()
    if field == "type":
        return [
            "{} Filter Type A".format(band),
            "{} Filter Type".format(band),
            "{} Mode A".format(band),
            "{} Mode".format(band),
        ]
    if field in {"frequency", "freq"}:
        return [
            "{} Frequency A".format(band),
            "{} Frequency".format(band),
        ]
    if field == "gain":
        return [
            "{} Gain A".format(band),
            "{} Gain".format(band),
        ]
    if field == "q":
        return [
            "{} Q A".format(band),
            "{} Q".format(band),
        ]
    return []


def resolve_parameter(
    parameters: Sequence[Any],
    device: Any,
    query: Any,
    curated_aliases: Dict[str, Tuple[str, ...]],
) -> Tuple[Any, ResolutionTrace]:
    query_text = str(query or "")
    normalized_query = normalize_query(query_text)
    if not normalized_query:
        return None, ResolutionTrace(
            matched_by=None,
            query=query_text,
            normalized_query=normalized_query,
            candidate_chain=[],
            resolved_param_name=None,
        )

    index = build_parameter_index(parameters)
    candidate_chain: List[str] = [query_text]

    exact = index.get(normalized_query)
    if exact is not None:
        return exact, ResolutionTrace(
            matched_by="exact",
            query=query_text,
            normalized_query=normalized_query,
            candidate_chain=candidate_chain,
            resolved_param_name=str(getattr(exact, "name", "")),
        )

    if _is_eq_like(device):
        for candidate in eq_band_rule_candidates(normalized_query):
            candidate_chain.append(candidate)
            matched = index.get(normalize_query(candidate))
            if matched is not None:
                return matched, ResolutionTrace(
                    matched_by="rule",
                    query=query_text,
                    normalized_query=normalized_query,
                    candidate_chain=candidate_chain,
                    resolved_param_name=str(getattr(matched, "name", "")),
                )

    for candidate in curated_aliases.get(normalized_query, ()):
        candidate_chain.append(candidate)
        matched = index.get(normalize_query(candidate))
        if matched is not None:
            return matched, ResolutionTrace(
                matched_by="alias",
                query=query_text,
                normalized_query=normalized_query,
                candidate_chain=candidate_chain,
                resolved_param_name=str(getattr(matched, "name", "")),
            )

    return None, ResolutionTrace(
        matched_by=None,
        query=query_text,
        normalized_query=normalized_query,
        candidate_chain=candidate_chain,
        resolved_param_name=None,
    )


def _is_eq_like(device: Any) -> bool:
    device_name = normalize_query(getattr(device, "name", ""))
    device_class = normalize_query(getattr(device, "class_name", ""))
    return device_name == "eqeight" or device_class in {"eq8", "eqeight"}
