"""
clinical_engine.py — Medication Orchestra

The deterministic safety core. Every clinical fact the product asserts is
produced here, from the curated knowledge base, with no language model in the
decision path.

Responsibilities:
  1. Pairwise interaction detection over *ingredient sets* (not drug-name
     strings), so combination products are handled correctly.
  2. Duplicate-ingredient detection across a patient's products, with a daily
     dose ledger against per-ingredient ceilings.
  3. Patient scoping: medications are grouped by profile and are NEVER paired
     across two different people in the same household.
  4. Coverage accounting: every medication that could not be resolved is
     reported, so the API can never imply "checked and clear" for a drug it did
     not understand.
  5. Deterministic schedule construction and verification: dose slots are
     computed from the interaction gap constraints, then re-verified. The
     verifier is independent of the builder and is the gate before persistence.

The LLM is used elsewhere, only to phrase explanations. It cannot change a
severity, a gap, a dose slot, or a safety claim.
"""

from __future__ import annotations

import itertools
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from services.medication_registry import (
    IngredientAmount,
    MedicationRegistry,
    ResolvedMedication,
    get_registry,
)

logger = logging.getLogger(__name__)

SEVERITY_ORDER = {"contraindicated": 0, "major": 1, "moderate": 2, "minor": 3, "info": 4}

#: De-duplicated ingredients are reported at most once per patient per check.
MAX_ALERTS_PER_PATIENT = 40


class AlertList(list):
    """A list of alerts that also carries how many were held back.

    Severity-ordered truncation never drops a contraindicated or major finding
    (those are always kept), so `truncated_count` only ever counts lower-severity
    findings the cap pushed out. The number is disclosed to the caller so the UI
    can say "showing 40 of 47" instead of silently hiding seven findings.
    """

    truncated_count: int = 0


# ── Alert model ───────────────────────────────────────────────────────────────

@dataclass
class Alert:
    """A single, independently auditable finding."""
    kind: str                      # "interaction" | "duplicate_ingredient" | "dose_ceiling" | "duplicate_therapy" | "advisory"
    severity: str                  # contraindicated | major | moderate | minor | info
    title: str
    detail: str
    action: str
    source: str
    citation: str
    rule_id: str
    profile_id: str = ""
    patient_name: str = ""
    medications: list[str] = field(default_factory=list)   # brand names involved
    ingredients: list[str] = field(default_factory=list)   # ingredient ids involved
    med_ids: list[str] = field(default_factory=list)
    #: Exactly which products conflict with which, and over which ingredients.
    #: The scheduler builds its constraints from this, so a merged alert cannot
    #: over-constrain products that merely share an ingredient without being the
    #: ones that interact.
    conflicting_pairs: list[dict] = field(default_factory=list)
    time_gap_hours: float = 0.0

    @property
    def severity_rank(self) -> int:
        return SEVERITY_ORDER.get(self.severity, 99)

    @property
    def interaction_id(self) -> str:
        """Stable id: same clinical fact => same id, across runs and devices.

        This is what makes acknowledgement, SOS deep-linking and audit trails
        possible (the previous implementation sent an id the payload never had).
        """
        parts = sorted(self.ingredients) or sorted(self.medications)
        return f"{self.kind}:{self.rule_id}:{self.profile_id}:{'+'.join(parts)}"

    def to_dict(self) -> dict:
        return {
            "id": self.interaction_id,
            "kind": self.kind,
            "severity": self.severity,
            "title": self.title,
            "detail": self.detail,
            "action": self.action,
            "source": self.source,
            "citation": self.citation,
            "rule_id": self.rule_id,
            "profile_id": self.profile_id,
            "patient_name": self.patient_name,
            "medications": list(self.medications),
            "ingredients": list(self.ingredients),
            "med_ids": list(self.med_ids),
            "conflicting_pairs": [dict(p) for p in self.conflicting_pairs],
            "time_gap_hours": self.time_gap_hours,
            "explanation": self.detail,        # kept for client compatibility
            "what_to_do": self.action,         # kept for client compatibility
            "med_a_name": self.medications[0] if self.medications else "",
            "med_b_name": self.medications[1] if len(self.medications) > 1 else "",
        }


@dataclass
class UncheckedMedication:
    """A medication that could not be fully evaluated. Must be shown to the user."""
    med_id: str
    profile_id: str
    brand_name: str
    generic_name: str
    reason: str
    unresolved_parts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "med_id": self.med_id,
            "profile_id": self.profile_id,
            "brand_name": self.brand_name,
            "generic_name": self.generic_name,
            "reason": self.reason,
            "unresolved_parts": list(self.unresolved_parts),
        }


# ── Patient-scoped check ──────────────────────────────────────────────────────

def group_by_patient(medications: Iterable[dict]) -> dict[str, list[dict]]:
    """Group medications by profile. A missing profile is its own bucket.

    Pairing two people's medications is clinically meaningless and drowns the
    caregiver in false alarms; this function is the only place grouping happens.
    """
    grouped: dict[str, list[dict]] = {}
    for med in medications:
        profile_id = (med.get("profile_id") or "default").strip() or "default"
        grouped.setdefault(profile_id, []).append(med)
    return grouped


def check_patient(
    medications: list[dict],
    profile_id: str,
    patient_name: str = "",
    registry: MedicationRegistry | None = None,
) -> tuple[list[Alert], list[UncheckedMedication]]:
    """Run every deterministic check for ONE patient's medication list."""
    registry = registry or get_registry()
    alerts: list[Alert] = []
    unchecked: list[UncheckedMedication] = []

    resolved: list[tuple[dict, ResolvedMedication]] = []
    for med in medications:
        res = registry.resolve_medication(med.get("brand_name"), med.get("generic_name"))
        res_dict = res.to_dict()
        med["ingredients"] = res_dict["ingredients"]
        med["resolved_by"] = res.resolved_by
        med["resolution_confidence"] = res.confidence
        if not res.ingredients:
            unchecked.append(UncheckedMedication(
                med_id=med.get("id", ""),
                profile_id=profile_id,
                brand_name=med.get("brand_name", ""),
                generic_name=med.get("generic_name", ""),
                reason="ingredients_not_identified",
                unresolved_parts=res.unresolved or [med.get("brand_name", "")],
            ))
        elif res.unresolved:
            unchecked.append(UncheckedMedication(
                med_id=med.get("id", ""),
                profile_id=profile_id,
                brand_name=med.get("brand_name", ""),
                generic_name=med.get("generic_name", ""),
                reason="partially_identified",
                unresolved_parts=list(res.unresolved),
            ))
        resolved.append((med, res))

    # -- 1. pairwise interactions, over ingredient sets ------------------------
    alerts.extend(_interaction_alerts(resolved, profile_id, patient_name, registry))

    # -- 2. duplicate active ingredient across different products -------------
    alerts.extend(_duplicate_alerts(resolved, profile_id, patient_name, registry))

    # -- 2b. duplicate therapy across the same mechanism group ---------------
    # Different ingredients (ibuprofen + diclofenac) that the exact-ingredient
    # check cannot see; uses the interaction rules' declared groups at runtime.
    alerts.extend(_duplicate_therapy_alerts(resolved, profile_id, patient_name, registry))

    # -- 3. single-ingredient advisories --------------------------------------
    alerts.extend(_advisory_alerts(resolved, profile_id, patient_name, registry))

    alerts.sort(key=lambda a: (a.severity_rank, a.title))
    # Never silently drop a critical/major finding: a flat cap that hid a
    # contraindication would be the worst possible defect. Keep every
    # contraindicated + major finding, then fill the remaining budget with
    # lower-severity findings in severity order, and disclose how many were
    # held back.
    must_keep = [a for a in alerts if a.severity_rank <= SEVERITY_ORDER["major"]]
    rest = [a for a in alerts if a.severity_rank > SEVERITY_ORDER["major"]]
    budget = max(0, MAX_ALERTS_PER_PATIENT - len(must_keep))
    kept = must_keep + rest[:budget]
    kept.sort(key=lambda a: (a.severity_rank, a.title))
    result = AlertList(kept)
    result.truncated_count = len(alerts) - len(kept)
    return result, unchecked


def _interaction_alerts(
    resolved: list[tuple[dict, ResolvedMedication]],
    profile_id: str,
    patient_name: str,
    registry: MedicationRegistry,
) -> list[Alert]:
    """Pairwise interaction alerts over ingredient sets.

    Alerts are keyed by (rule, pair of mechanism groups) and then list the
    products that actually carry the triggering ingredients. Keying by group
    rather than by ingredient matters in three directions:

      * the same clinical fact reaching the patient through three different
        brands is one alert, not three;
      * warfarin + ibuprofen and warfarin + aspirin are the same clinical fact
        ("do not combine this blood thinner with a painkiller of this class"),
        so they are one alert naming all four products, not two;
      * a second product containing a triggering ingredient is never swallowed
        by de-duplication - it is added to the same alert.

    Only medicines containing one of the triggering ingredients are listed, so
    an alert about warfarin + ibuprofen does not name a thyroid tablet that
    happens to be in the same medicine list.
    """
    grouped: dict[tuple[str, frozenset], dict] = {}

    for (med_a, res_a), (med_b, res_b) in itertools.combinations(resolved, 2):
        for ing_a in sorted(res_a.ingredient_ids):
            for ing_b in sorted(res_b.ingredient_ids):
                if ing_a == ing_b:
                    continue
                for rule in registry.rules_for_pair(ing_a, ing_b):
                    group_a = registry.group_of(rule, ing_a)
                    group_b = registry.group_of(rule, ing_b)
                    # A rule only ever connects two different mechanism groups;
                    # see MedicationRegistry._load.
                    if group_a == group_b:
                        continue
                    key = (rule["id"], frozenset((group_a, group_b)))
                    bucket = grouped.setdefault(key, {
                        "rule": rule,
                        "ingredients": [],
                        "meds": [],
                        "med_ids": [],
                        "pairs": [],
                    })
                    for ingredient_id in (ing_a, ing_b):
                        if ingredient_id not in bucket["ingredients"]:
                            bucket["ingredients"].append(ingredient_id)
                    pair = {
                        "med_ids": [med_a.get("id", ""), med_b.get("id", "")],
                        "ingredients": [ing_a, ing_b],
                    }
                    if pair not in bucket["pairs"]:
                        bucket["pairs"].append(pair)
                    for med in (med_a, med_b):
                        med_id = med.get("id", "")
                        if med_id not in bucket["med_ids"]:
                            bucket["med_ids"].append(med_id)
                            bucket["meds"].append(med)

    alerts: list[Alert] = []
    for bucket in grouped.values():
        # Deterministic ordering of the reporting medications.
        meds = sorted(
            bucket["meds"],
            key=lambda m: (m.get("brand_name") or "", m.get("id") or ""),
        )
        alert = _rule_to_alert(
            bucket["rule"], sorted(bucket["ingredients"]), meds,
            profile_id, patient_name,
        )
        alert.conflicting_pairs = bucket["pairs"]
        alerts.append(alert)
    return alerts


def _rule_to_alert(
    rule: dict,
    ingredient_ids: list[str],
    meds: list[dict],
    profile_id: str,
    patient_name: str,
) -> Alert:
    severity = rule["severity"]
    return Alert(
        kind="interaction",
        severity=severity,
        title=rule["title"],
        detail=rule["mechanism"],
        action=rule["management"],
        source=rule.get("_source_label") or rule.get("source", ""),
        citation=rule.get("citation", ""),
        rule_id=rule["id"],
        profile_id=profile_id,
        patient_name=patient_name,
        medications=[m.get("brand_name", "") for m in meds],
        ingredients=ingredient_ids,
        med_ids=[m.get("id", "") for m in meds],
        time_gap_hours=rule_time_gap(rule),
    )


#: Minimum separation between two interacting doses, by severity. These exist
#: only as a last-resort default; the solver NEVER fabricates a non-zero gap a
#: rule's cited advice does not support. Every shipped rule declares its own
#: `min_gap_hours` (0.0 where timing cannot resolve the combination), so the
#: severity default is 0.0 for every severity - a rule missing `min_gap_hours`
#: is a knowledge-base defect and yields no separation, never an invented one.
DEFAULT_TIME_GAPS: dict[str, float] = {
    "contraindicated": 0.0,   # never co-administer; scheduling cannot fix this
    "major": 0.0,
    "moderate": 0.0,
    "minor": 0.0,
    "info": 0.0,
}


def rule_time_gap(rule: dict) -> float:
    """How far apart this rule's two medicines must be kept.

    A rule declares `min_gap_hours` when its own cited advice names a specific
    interval - the aspirin/NSAID rule says 8 hours, and the schedule must not
    print "kept apart" over a gap the citation does not support. A rule that
    declares 0.0 means no time separation resolves the combination (it is a
    standing alert, not a timing one). A rule missing `min_gap_hours` entirely
    is an incomplete knowledge-base entry; the solver refuses to invent a gap
    for it and treats the pair as un-separable by timing (0.0), never a
    fabricated severity-based hour count.
    """
    declared = rule.get("min_gap_hours")
    if isinstance(declared, (int, float)) and declared >= 0:
        return float(declared)
    return DEFAULT_TIME_GAPS.get(rule.get("severity", ""), 0.0)


def _duplicate_alerts(
    resolved: list[tuple[dict, ResolvedMedication]],
    profile_id: str,
    patient_name: str,
    registry: MedicationRegistry,
) -> list[Alert]:
    """Two different products containing the same active ingredient.

    This is the most common preventable medication harm in the target
    population (paracetamol double-dosing) and was previously invisible: the old
    engine skipped any pair whose generic strings were equal, and had no notion
    of ingredient sets at all.
    """
    alerts: list[Alert] = []
    by_ingredient: dict[str, list[tuple[dict, IngredientAmount]]] = {}
    for med, res in resolved:
        for amount in res.ingredients:
            by_ingredient.setdefault(amount.ingredient_id, []).append((med, amount))

    for ingredient_id, entries in by_ingredient.items():
        distinct_products = {e[0].get("brand_name", "") for e in entries}
        if len(distinct_products) < 2:
            continue

        ingredient_entry = registry.ingredients[ingredient_id]
        ceiling = registry.ceiling_for(ingredient_id)
        total_mg = sum(a.mg for _, a in entries if a.mg is not None)
        any_unknown_strength = any(a.mg is None for _, a in entries)

        # Per-product breakdown, so the user can see exactly which tablet
        # contributes what - this is the number that makes the alert actionable.
        breakdown = "; ".join(
            f"{m.get('brand_name', '')}"
            + (f" {a.mg:g} mg" if a.mg is not None else " (strength not read)")
            for m, a in sorted(entries, key=lambda e: e[0].get("brand_name", ""))
            if m.get("id") in {x[0].get("id") for x in entries}
        )

        # Daily exposure, where the prescription tells us how many doses a day
        # are taken. A single dose-time total below the ceiling can still add up
        # to a daily total above it - which is the number that matters.
        daily_mg = 0.0
        daily_complete = True
        for med_entry, amount in entries:
            doses, _ = _doses_per_day(med_entry)
            if amount.mg is None or doses <= 0:
                daily_complete = False
                continue
            daily_mg += amount.mg * doses

        if ceiling:
            daily_clause = ""
            if daily_mg and daily_complete:
                limit = ceiling["elderly_ceiling_mg_per_day"]
                daily_clause = (
                    f" Taken as prescribed - {daily_mg:g} mg a day - that is "
                    + (
                        f"over the {limit} mg daily limit used for older adults."
                        if daily_mg > limit else
                        f"below the {limit} mg daily limit for older adults, but the same "
                        "medicine is arriving from more than one tablet."
                    )
                )
            detail = (
                f"{len(distinct_products)} of these products contain "
                f"{ingredient_entry['name']}, so the doses add up: {breakdown}. "
                f"That is {total_mg:g} mg per dose-time"
                f"{' (at least - some strengths were not read)' if any_unknown_strength else ''}."
                f"{daily_clause} Ceiling used for this check: "
                f"{ceiling['elderly_ceiling_mg_per_day']} mg per day (older adults)."
            ) if total_mg else (
                f"{len(distinct_products)} of these products contain "
                f"{ingredient_entry['name']}, so the same medicine is being taken more than "
                f"once per dose-time: {breakdown}."
            )
            citation = ceiling.get("citation", "")
            source = registry.sources.get(ceiling.get("source", ""), ceiling.get("source", ""))
        else:
            detail = (
                f"{len(distinct_products)} of these products contain "
                f"{ingredient_entry['name']} ({breakdown}). Taking them at the same time "
                f"repeats the same medicine."
            )
            citation = "Ingredient composition as recorded in the product's own labelling"
            source = "FDA_LABEL"

        alerts.append(Alert(
            kind="duplicate_ingredient" if not ceiling else "dose_ceiling",
            severity="major" if ceiling else "moderate",
            title=f"Same medicine in {len(distinct_products)} products: {ingredient_entry['name']}",
            detail=detail,
            action=(
                "Check the full list with a pharmacist or the prescriber before the next dose. "
                "Do not take two products with the same ingredient at the same time unless a "
                "doctor has specifically told you to."
            ),
            source=source,
            citation=citation,
            rule_id=f"dup_{ingredient_id}",
            profile_id=profile_id,
            patient_name=patient_name,
            medications=sorted(distinct_products),
            ingredients=[ingredient_id],
            med_ids=[e[0].get("id", "") for e in entries],
            conflicting_pairs=[
                {"med_ids": [a[0].get("id", ""), b[0].get("id", "")], "ingredients": [ingredient_id]}
                for a, b in itertools.combinations(entries, 2)
                if a[0].get("id") != b[0].get("id")
            ],
        ))
    return alerts


def _duplicate_therapy_alerts(
    resolved: list[tuple[dict, ResolvedMedication]],
    profile_id: str,
    patient_name: str,
    registry: MedicationRegistry,
) -> list[Alert]:
    """Two different products whose active ingredients share a mechanism group.

    Exact-ingredient matching (``_duplicate_alerts``) catches two brands of
    ibuprofen; it misses ibuprofen + diclofenac, which are different ingredients
    with the same NSAID mechanism. The interaction rules already declare these
    groups (``rule["groups"]``); this check uses them at runtime so a
    duplicate-therapy finding reaches the patient.

    Severity is ``moderate`` — the groups are coarse and a clinician (B-1) has
    not reviewed every boundary, so this never blocks a schedule on its own.
    It works on resolved ingredient ids only, never on brand-name strings, so
    it reintroduces no fuzzy matching.
    """
    alerts: list[Alert] = []

    # cluster root -> med_id -> (med entry, ingredient ids in this cluster)
    by_cluster: dict[str, dict[str, tuple[dict, set[str]]]] = {}
    for med, res in resolved:
        med_id = med.get("id") or med.get("brand_name", "")
        for iid in res.ingredient_ids:
            root = registry.mechanism_group_of(iid)
            if root is None:
                continue
            bucket = by_cluster.setdefault(root, {})
            entry = bucket.setdefault(med_id, (med, set()))
            entry[1].add(iid)

    for root, products in by_cluster.items():
        if len(products) < 2:
            continue  # one product -> nothing to duplicate against
        ingredients_here = {iid for _, ings in products.values() for iid in ings}
        if len(ingredients_here) < 2:
            # Every product maps to the same single ingredient: that is the
            # exact-duplicate case already covered by _duplicate_alerts, so
            # reporting it again here would double-alert the same fact.
            continue

        info = registry.mechanism_cluster_info(root)
        ordered = sorted(products.values(), key=lambda kv: (kv[0].get("brand_name") or "", kv[0].get("id") or ""))
        med_entries = [m for m, _ in ordered]
        med_ids = [m.get("id", "") for m, _ in ordered]
        brand_names = sorted({m.get("brand_name", "") for m in med_entries})
        ingredient_names = sorted(
            registry.ingredients[iid]["name"] for iid in ingredients_here
        )

        pairs = [
            {
                "med_ids": [a[0].get("id", ""), b[0].get("id", "")],
                "ingredients": sorted(a[1] | b[1]),
            }
            for a, b in itertools.combinations(ordered, 2)
        ]

        sources = info["sources"] or ["Interaction knowledge base"]
        rule_clause = ", ".join(info["rule_ids"]) or "(no rule id)"
        alerts.append(Alert(
            kind="duplicate_therapy",
            severity="moderate",
            title=(
                f"Same type of medicine in {len(brand_names)} products: "
                + " + ".join(ingredient_names)
            ),
            detail=(
                f"{len(brand_names)} products contain different ingredients that "
                f"work the same way ({', '.join(ingredient_names)}): "
                f"{', '.join(brand_names)}. Taking two medicines of the same "
                f"type at the same time repeats the effect and is usually "
                f"unintentional. Mechanism group defined by interaction "
                f"rule(s): {rule_clause}."
            ),
            action=(
                "Check with a pharmacist or prescriber whether both are still "
                "needed. Do not stop or change either on your own."
            ),
            source="; ".join(sources),
            citation=f"Mechanism-group definition in interaction knowledge base "
                     f"(rules: {rule_clause})",
            rule_id=f"duptherapy_{root}",
            profile_id=profile_id,
            patient_name=patient_name,
            medications=brand_names,
            ingredients=sorted(ingredients_here),
            med_ids=med_ids,
            conflicting_pairs=pairs,
        ))
    return alerts


def _advisory_alerts(
    resolved: list[tuple[dict, ResolvedMedication]],
    profile_id: str,
    patient_name: str,
    registry: MedicationRegistry,
) -> list[Alert]:
    alerts: list[Alert] = []
    present = {iid for _, res in resolved for iid in res.ingredient_ids}
    for ingredient_id in sorted(present):
        for advisory in registry.advisories_for(ingredient_id):
            if not set(advisory["_ingredients"]).issubset(present):
                continue
            alerts.append(Alert(
                kind="advisory",
                severity=advisory.get("severity", "info"),
                title=advisory["title"],
                detail=advisory["mechanism"],
                action=advisory["management"],
                source=advisory.get("_source_label") or advisory.get("source", ""),
                citation=advisory.get("citation", ""),
                rule_id=advisory["id"],
                profile_id=profile_id,
                patient_name=patient_name,
                medications=[m.get("brand_name", "") for m, r in resolved
                             if ingredient_id in r.ingredient_ids],
                ingredients=sorted(advisory["_ingredients"]),
                med_ids=[m.get("id", "") for m, r in resolved
                         if ingredient_id in r.ingredient_ids],
            ))
    return alerts


# ── Household-level check ─────────────────────────────────────────────────────

def check_household(
    medications: list[dict],
    profile_names: dict[str, str] | None = None,
    registry: MedicationRegistry | None = None,
) -> dict:
    """Check every patient independently and account for coverage.

    Returns a payload whose `coverage` block is the honest headline the UI must
    render: how many medications were fully checked, and which were not.
    """
    registry = registry or get_registry()
    profile_names = profile_names or {}
    grouped = group_by_patient(medications)

    all_alerts: list[Alert] = []
    all_unchecked: list[UncheckedMedication] = []
    per_patient: list[dict] = []

    for profile_id, meds in sorted(grouped.items()):
        name = profile_names.get(profile_id, "")
        alerts, unchecked = check_patient(meds, profile_id, name, registry)
        all_alerts.extend(alerts)
        all_unchecked.extend(unchecked)
        per_patient_truncated = getattr(alerts, "truncated_count", 0)
        per_patient.append({
            "profile_id": profile_id,
            "patient_name": name,
            "medication_count": len(meds),
            "checked_count": len(meds) - len(unchecked),
            "unchecked_count": len(unchecked),
            "alert_count": len(alerts),
            "truncated_count": per_patient_truncated,
            "critical_count": sum(
                1 for a in alerts if a.severity in ("contraindicated", "major")
            ),
        })

    all_alerts.sort(key=lambda a: (a.severity_rank, a.profile_id, a.title))

    total = len(medications)
    checked = total - len(all_unchecked)
    coverage = {
        "medications_total": total,
        "medications_fully_checked": checked,
        "medications_unchecked": len(all_unchecked),
        # With nothing to check, coverage is 0% - not 100%. Reporting "complete"
        # for an empty list is exactly how the old build produced a green
        # "nothing to worry about" screen for a user whose medicines had never
        # been loaded.
        "coverage_percent": round(100.0 * checked / total, 1) if total else 0.0,
        "is_complete": total > 0 and len(all_unchecked) == 0,
        "patients": per_patient,
    }
    critical = sum(1 for a in all_alerts if a.severity in ("contraindicated", "major"))
    truncated_total = sum(p["truncated_count"] for p in per_patient)
    return {
        "alerts": [a.to_dict() for a in all_alerts],
        "unchecked": [u.to_dict() for u in all_unchecked],
        "coverage": coverage,
        "critical_count": critical,
        "truncated_count": truncated_total,
        "max_alerts_per_patient": MAX_ALERTS_PER_PATIENT,
        "knowledge_base": registry.describe(),
        "review_status": registry.review_status,
    }


# ── Deterministic scheduling ──────────────────────────────────────────────────

DEFAULT_SLOT_TIMES = ("08:00", "13:00", "17:00", "20:00", "22:00")

#: Frequency codes -> (doses per day, preferred slot indices into DEFAULT_SLOT_TIMES)
FREQUENCY_SLOTS: dict[str, tuple[int, tuple[int, ...]]] = {
    "od": (1, (0,)),
    "once daily": (1, (0,)),
    "bd": (2, (0, 3)),
    "twice daily": (2, (0, 3)),
    "tds": (3, (0, 1, 3)),
    "three times daily": (3, (0, 1, 3)),
    "qid": (4, (0, 1, 2, 3)),
    "four times daily": (4, (0, 1, 2, 3)),
    "hs": (1, (4,)),
    "bedtime": (1, (4,)),
    "sos": (0, ()),
    "as needed": (0, ()),
    "stat": (0, ()),
}

_BEFORE_MEALS = ("ac", "before food", "before meals", "empty stomach", "before breakfast")
_AFTER_MEALS = ("pc", "after food", "after meals", "after breakfast", "after dinner")


def _minutes(hhmm: str) -> int:
    hours, _, minutes = hhmm.partition(":")
    return int(hours) * 60 + int(minutes or 0)


def _hhmm(total_minutes: int) -> str:
    total_minutes %= 24 * 60
    return f"{total_minutes // 60:02d}:{total_minutes % 60:02d}"


def _doses_per_day(med: dict) -> tuple[int, tuple[str, ...]]:
    """How many doses a day, and the patient's preferred times for them.

    Returns (0, ()) for as-needed medicines, which are not scheduled.
    """
    explicit = [str(t) for t in (med.get("timing") or []) if t]
    if explicit:
        times = tuple(sorted(explicit))
        return len(times), times

    raw = str(med.get("frequency_raw") or "").strip().lower()
    english = str(med.get("frequency_english") or "").strip().lower()
    for key in (raw, english):
        key = re.sub(r"\s+", " ", key)
        if key in FREQUENCY_SLOTS:
            count, slots = FREQUENCY_SLOTS[key]
            return count, tuple(DEFAULT_SLOT_TIMES[i] for i in slots)

    # Unknown frequency: schedule once daily and say so.
    return 1, (DEFAULT_SLOT_TIMES[0],)


def _feasibility_conflict(
    med_id: str,
    slot_time: str,
    assignments: dict[str, list[str]],
    gap_pairs: dict[frozenset, float],
    no_coallocate_pairs: set[frozenset],
) -> str | None:
    """Why `med_id` cannot go in `slot_time`, or None if it is safe there.

    Checks BOTH constraints that matter clinically:
      * two products sharing an active ingredient, or any interacting pair, must
        not share a slot (co-administration is the riskiest moment for a
        combination alert, even one timing cannot resolve), and
      * an interacting pair with a cited `min_gap_hours` must be separated by at
        least that many hours from *every* dose of the other medicine, not just
        the one in this slot.
    """
    for other_id, other_slots in assignments.items():
        if other_id == med_id:
            if slot_time in other_slots:
                return "this medicine is already scheduled at that time"
            continue

        pair = frozenset((med_id, other_id))
        if pair in no_coallocate_pairs and slot_time in other_slots:
            return f"another medicine that interacts with this one is already at {slot_time}"

        gap = gap_pairs.get(pair, 0.0)
        if gap > 0:
            for other_time in other_slots:
                separation = abs(_minutes(slot_time) - _minutes(other_time)) / 60.0
                if separation < gap:
                    return (
                        f"only {separation:g}h from a medicine needing a {gap:g}h gap"
                        f" ({other_time})"
                    )
    return None


def build_schedule(
    medications: list[dict],
    alerts: list[dict],
    profile_id: str,
    registry: MedicationRegistry | None = None,
) -> dict:
    """Construct a dose schedule deterministically, honouring severity gaps.

    Guarantees:
      * Every medicine gets the number of doses its prescription implies, placed
        in distinct, conflict-free slots - or it is reported as unscheduled.
      * Two products sharing an active ingredient never share a slot (that would
        silently double the dose).
      * Interacting pairs are separated by at least the required gap from every
        dose of the other medicine, or reported as a conflict.
      * `verify_schedule()` re-checks the finished payload independently, and the
        schedule status reflects the outcome.

    No language model participates. The returned `safety_notes` are generated
    from the verified constraint set, never authored by a model.
    """
    registry = registry or get_registry()
    patient_alerts = [a for a in alerts if a.get("profile_id") == profile_id]
    active = [
        m for m in medications
        if (m.get("status") or "active") == "active"
    ]
    # Deterministic order: stable across runs for the same input.
    active.sort(key=lambda m: (m.get("id") or m.get("brand_name") or ""))

    gap_pairs: dict[frozenset, float] = {}
    # Pairs that must never be co-administered in the same slot: duplicates
    # (same active ingredient) AND every interaction pair. An interaction with
    # no cited time gap (min_gap_hours == 0) is a standing combination alert that
    # timing cannot resolve - the two are still never placed in the same slot,
    # but no fabricated hour gap is claimed for them.
    no_coallocate_pairs: set[frozenset] = set()

    for alert in patient_alerts:
        pairs = alert.get("conflicting_pairs") or []
        if not pairs:
            # Fall back to the display list only when the alert has no explicit
            # pair structure (e.g. an advisory built by an older code path).
            ids = alert.get("med_ids") or []
            pairs = [{"med_ids": pair} for pair in itertools.combinations(ids, 2)]
        for pair in pairs:
            ids = [i for i in (pair.get("med_ids") or []) if i]
            if len(ids) < 2 or ids[0] == ids[1]:
                continue
            key = frozenset(ids)
            if alert.get("kind") in ("duplicate_ingredient", "dose_ceiling", "duplicate_therapy"):
                no_coallocate_pairs.add(key)
            elif alert.get("kind") == "interaction":
                # Two interacting medicines are never co-administered in the same
                # slot, regardless of whether a cited hour gap also applies.
                no_coallocate_pairs.add(key)
                gap = float(alert.get("time_gap_hours") or 0.0)
                if gap > 0:
                    gap_pairs[key] = max(gap_pairs.get(key, 0.0), gap)

    assignments: dict[str, list[str]] = {}
    unscheduled: list[dict] = []
    moves: list[dict] = []

    for med in active:
        med_id = med.get("id") or med.get("brand_name", "")
        doses, preferred = _doses_per_day(med)
        if doses == 0:
            continue
        placed: list[str] = []

        for dose_index in range(doses):
            preferred_time = preferred[dose_index] if dose_index < len(preferred) else None
            # Candidate order: this dose's own time first, then the remaining
            # preferred times, then every other slot in the day.
            candidates: list[str] = []
            if preferred_time:
                candidates.append(preferred_time)
            candidates.extend(t for t in preferred if t not in candidates)
            candidates.extend(t for t in DEFAULT_SLOT_TIMES if t not in candidates)

            chosen = None
            for candidate in candidates:
                if _feasibility_conflict(med_id, candidate, assignments, gap_pairs, no_coallocate_pairs) is None:
                    chosen = candidate
                    break
            if chosen is None:
                # One entry per medicine, not per dose: the client counts these
                # entries, and a twice-daily medicine with nowhere to go is one
                # problem. The "x of y doses" detail lives in `conflicts`.
                _record_unscheduled(
                    unscheduled, med_id, med.get("brand_name", ""),
                    dose_index + 1, preferred_time or "",
                )
                continue
            placed.append(chosen)
            assignments.setdefault(med_id, []).append(chosen)
            if chosen != preferred_time:
                logger.info(
                    "Schedule: dose %d of %s moved %s -> %s to keep the medicines safe",
                    dose_index + 1, med.get("brand_name"), preferred_time, chosen,
                )
                moves.append({
                    "med_id": med_id,
                    "med_name": med.get("brand_name", ""),
                    "dose_index": dose_index + 1,
                    "from": preferred_time or "",
                    "to": chosen,
                })

        if not placed:
            _record_unscheduled(
                unscheduled, med_id, med.get("brand_name", ""), 1,
                preferred[0] if preferred else "",
            )

    dose_times = []
    for slot_time in DEFAULT_SLOT_TIMES:
        meds_here = [
            m for m in active
            if slot_time in assignments.get(m.get("id") or m.get("brand_name", ""), [])
        ]
        if not meds_here:
            continue
        dose_times.append({
            "time": slot_time,
            "label": _slot_label(slot_time),
            "medications": [
                {
                    "med_id": m.get("id", ""),
                    "med_name": m.get("brand_name", ""),
                    "generic_name": m.get("generic_name", ""),
                    "dose": m.get("dosage") or "as prescribed",
                    "instruction": m.get("instruction", ""),
                    "ingredients": [i["ingredient_id"] for i in m.get("ingredients", [])],
                    "resolution_confidence": m.get("resolution_confidence", "low"),
                    "interaction_warning": None,
                }
                for m in meds_here
            ],
        })

    # Any medicine whose ingredients are unknown can never be declared safe in a
    # slot, so it is placed last and flagged rather than silently co-located.
    unknown = [m for m in active if m.get("resolution_confidence") == "low"]
    for item in dose_times:
        for entry in item["medications"]:
            if any(u.get("id") == entry["med_id"] for u in unknown):
                entry["interaction_warning"] = (
                    "Ingredients could not be read - timing not checked against other medicines"
                )

    safety_notes, conflicts = _safety_notes(patient_alerts, dose_times, unscheduled)
    scheduled_ids = set(assignments)
    for move in moves:
        # A move note explains what the dose was moved *away from*. A medicine
        # that could not be placed at all is reported in `unscheduled`, so naming
        # it here would read as "keep 6 hours from Ecosprin" when Ecosprin is not
        # in the schedule at all.
        reason = _move_reason(move["med_id"], patient_alerts, scheduled_ids)
        safety_notes.append(
            f"{move['med_name']} (dose {move['dose_index']}) was moved from "
            f"{move['from']} to {move['to']}{reason}."
            if move["from"] else
            f"{move['med_name']} (dose {move['dose_index']}) is scheduled at {move['to']}{reason}."
        )
    schedule = {
        "profile_id": profile_id,
        "dose_times": dose_times,
        "total_dose_times": len(dose_times),
        "unscheduled": unscheduled,
        "conflicts": conflicts,
        "safety_notes": safety_notes,
        # "verified"       - every prescribed dose placed, no constraint violated
        # "partial"        - some doses could not be placed safely (see conflicts)
        # "unsafe_conflict"- the independent verifier found a violated constraint
        "schedule_status": "partial" if conflicts else "verified",
        "generated_by": "deterministic_solver",
        "knowledge_base": registry.versions,
        "review_status": registry.review_status,
        "unchecked_medications": [
            {
                "med_id": m.get("id", ""),
                "med_name": m.get("brand_name", ""),
                "note": (
                    "This medicine's ingredients could not be identified, so its timing has "
                    "not been checked against the others."
                ),
            }
            for m in unknown
        ],
    }
    verification = verify_schedule(schedule, patient_alerts)
    schedule["verification"] = verification
    if verification["violations"]:
        # The verifier is independent of the builder: if it disagrees, the
        # schedule is not safe to present as verified, whatever the builder thought.
        logger.error(
            "Schedule verification FAILED for profile %s: %s",
            profile_id, verification["violations"],
        )
        schedule["schedule_status"] = "unsafe_conflict"
        schedule["conflicts"] = list(schedule["conflicts"]) + [
            {
                "alert_id": v.get("alert_id", ""),
                "severity": "major",
                "required_gap_hours": v.get("required_gap_hours", 0),
                "medications": [],
                "reason": v.get("reason", "verification_failed"),
                "message": v.get("message", "This schedule could not be verified as safe."),
            }
            for v in verification["violations"]
        ]
    return schedule



def _move_reason(
    med_id: str, alerts: list[dict], scheduled_ids: set[str] | None = None
) -> str:
    """Human-readable reason a dose was moved, from the alert that forced it.

    `scheduled_ids`, when given, restricts the explanation to the medicines that
    are actually in the timetable.
    """
    for alert in alerts:
        if med_id in (alert.get("med_ids") or []):
            ids = alert.get("med_ids") or []
            names = alert.get("medications") or []
            # Report the *other* medicines, not the one being moved.
            others = [n for n, i in zip(names, ids, strict=False) if i != med_id] or names
            if scheduled_ids is not None:
                placed = [
                    n for n, i in zip(names, ids, strict=False) if i in scheduled_ids
                ]
                if placed:
                    others = placed
            if alert.get("kind") in ("duplicate_ingredient", "dose_ceiling"):
                return (
                    " because it contains the same active ingredient as another medicine "
                    "you take, so the two must not be taken at the same time"
                )
            if alert.get("kind") == "duplicate_therapy":
                return (
                    " because it works the same way as another medicine you take "
                    "(same mechanism group), so the two must not be taken at the same time"
                )
            if alert.get("kind") == "interaction" and float(alert.get("time_gap_hours") or 0) > 0:
                gap = float(alert["time_gap_hours"])
                return f" to keep at least {gap:g} hours from another medicine ({', '.join(others[:2])})"
    return " to avoid a timing conflict"


def _slot_label(slot: str) -> str:
    return {
        "08:00": "Morning (with breakfast)",
        "13:00": "Afternoon (with lunch)",
        "17:00": "Evening (with tea)",
        "20:00": "Night (with dinner)",
        "22:00": "Bedtime",
    }.get(slot, f"Dose at {slot}")


def _record_unscheduled(
    unscheduled: list[dict],
    med_id: str,
    med_name: str,
    dose_index: int,
    preferred_time: str,
) -> None:
    """Record that a medicine's dose could not be placed, once per medicine.

    A medicine that cannot be placed is one problem for the person reading the
    schedule, however many of its doses failed, and the client counts the entries
    in `unscheduled`. Entries are keyed by medicine and keep the first failing
    dose, which is the one the user would have taken first.
    """
    for item in unscheduled:
        if item["med_id"] == med_id:
            return
    unscheduled.append({
        "med_id": med_id,
        "med_name": med_name,
        "reason": "no_safe_slot_available",
        "dose_index": dose_index,
        "preferred_time": preferred_time,
    })


def _safety_notes(
    alerts: list[dict],
    dose_times: list[dict],
    unscheduled: list[dict],
) -> tuple[list[str], list[dict]]:
    """Build notes and conflicts from the *verified* constraint set.

    Notes are derived from the schedule that was actually produced. They are
    never authored by a language model, so a note can never contradict the
    timings it sits next to (the failure this replaces: a model-written "kept
    safely apart" beside two drugs in the same slot).
    """
    notes: list[str] = []
    conflicts: list[dict] = []
    timing = {dt["time"]: [m["med_id"] for m in dt["medications"]] for dt in dose_times}
    names_by_id = {
        m["med_id"]: m["med_name"]
        for dt in dose_times for m in dt["medications"]
    }
    placed_counts: dict[str, int] = {}
    for dt in dose_times:
        for m in dt["medications"]:
            placed_counts[m["med_id"]] = placed_counts.get(m["med_id"], 0) + 1

    seen_pairs: set[frozenset] = set()
    for alert in alerts:
        if alert.get("kind") != "interaction":
            continue
        gap = float(alert.get("time_gap_hours") or 0.0)
        if gap <= 0:
            continue
        for pair in (alert.get("conflicting_pairs") or []):
            pair_ids = [i for i in (pair.get("med_ids") or []) if i]
            if len(pair_ids) < 2:
                continue
            key = frozenset(pair_ids) | frozenset(pair.get("ingredients") or [])
            if key in seen_pairs:
                continue
            seen_pairs.add(key)

            a, b = pair_ids[0], pair_ids[1]
            slots_a = [t for t, meds in timing.items() if a in meds]
            slots_b = [t for t, meds in timing.items() if b in meds]
            label_a = names_by_id.get(a, a)
            label_b = names_by_id.get(b, b)

            if not slots_a or not slots_b:
                continue  # one of them is not scheduled; reported separately

            shortest = min(
                abs(_minutes(x) - _minutes(y)) / 60.0 for x in slots_a for y in slots_b
            )
            if shortest >= gap:
                notes.append(
                    f"{label_a} and {label_b} are kept at least {gap:g} hours apart in this schedule."
                )
            else:
                conflicts.append({
                    "alert_id": alert.get("id", ""),
                    "severity": alert.get("severity"),
                    "required_gap_hours": gap,
                    "medications": [label_a, label_b],
                    "reason": "required_gap_not_satisfiable",
                    "message": (
                        f"{label_a} and {label_b} need at least {gap:g} hours between doses, "
                        f"which does not fit the times prescribed for them. Ask the prescriber or "
                        f"pharmacist how to sequence them - do not take them together."
                    ),
                })

    # One entry per medicine that could not get all its doses placed safely.
    by_med: dict[str, dict] = {}
    for item in unscheduled:
        entry = by_med.setdefault(item["med_id"], {
            "med_id": item["med_id"],
            "med_name": item["med_name"],
            "doses_not_placed": [],
            "preferred_times": [],
        })
        entry["doses_not_placed"].append(item.get("dose_index"))
        if item.get("preferred_time"):
            entry["preferred_times"].append(item["preferred_time"])

    for entry in by_med.values():
        times = ", ".join(t for t in entry["preferred_times"] if t)
        conflicts.append({
            "severity": "major",
            "required_gap_hours": 0,
            "medications": [entry["med_name"]],
            "reason": "no_safe_slot_available",
            "message": (
                f"{placed_counts.get(entry['med_id'], 0)} of {max(placed_counts.get(entry['med_id'], 0) + len(entry['doses_not_placed']), 1)} "
                f"prescribed doses of {entry['med_name']} could be placed in a conflict-free "
                f"slot today"
                + (f"; the doses due at {times} could not" if times else "")
                + ". Ask a pharmacist how to space this medicine around the others."
            ),
        })
    return notes, conflicts


def verify_schedule(schedule: dict, alerts: list[dict]) -> dict:
    """Independently re-check a schedule. This is the gate before persistence.

    Deliberately re-derives slot occupancy from the schedule payload rather than
    trusting the builder's bookkeeping, so a bug in `build_schedule` cannot
    produce a schedule that silently passes verification.

    The boolean is named ``verified_against_rules`` (not ``verified``) because it
    only means "no rule the verifier checked was violated" — it is not a medical
    safety claim. ``rules_checked`` lists which rules were examined and
    ``knowledge_version`` records which knowledge base the check ran against, so a
    cached schedule can be detected as stale after a rule is corrected.
    """
    violations: list[dict] = []
    dose_times = schedule.get("dose_times") or []
    slot_ids = {dt["time"]: [m.get("med_id", "") for m in dt.get("medications", [])]
                for dt in dose_times}
    checked_rules: list[str] = []

    for alert in alerts:
        ids = alert.get("med_ids") or []
        if len(ids) < 2:
            continue
        rule_id = alert.get("rule_id") or alert.get("id") or ""
        if rule_id and rule_id not in checked_rules:
            checked_rules.append(rule_id)
        kind = alert.get("kind")
        gap = float(alert.get("time_gap_hours") or 0.0)
        pairs = alert.get("conflicting_pairs") or [
            {"med_ids": p} for p in itertools.combinations(ids, 2)
        ]

        if kind in ("duplicate_ingredient", "dose_ceiling", "duplicate_therapy"):
            for pair in pairs:
                pair_ids = pair.get("med_ids") or []
                if len(pair_ids) < 2:
                    continue
                a, b = pair_ids[0], pair_ids[1]
                shared = [t for t, meds in slot_ids.items() if a in meds and b in meds]
                if shared:
                    if kind == "duplicate_therapy":
                        reason = "duplicate_therapy_same_slot"
                        message = (
                            f"Two products with the same mechanism are both "
                            f"scheduled at {shared}"
                        )
                    else:
                        reason = "duplicate_ingredient_same_slot"
                        message = (
                            f"Two products sharing an ingredient are both "
                            f"scheduled at {shared}"
                        )
                    violations.append({
                        "alert_id": alert.get("id", ""),
                        "kind": kind,
                        "reason": reason,
                        "slots": shared,
                        "message": message,
                    })
        elif kind == "interaction":
            for pair in pairs:
                pair_ids = pair.get("med_ids") or []
                if len(pair_ids) < 2:
                    continue
                a, b = pair_ids[0], pair_ids[1]
                slots_a = [t for t, meds in slot_ids.items() if a in meds]
                slots_b = [t for t, meds in slot_ids.items() if b in meds]
                # Two interacting medicines are never co-administered in the same
                # slot, even when no cited hour gap applies (a combination alert
                # timing cannot resolve is still a co-administration risk).
                shared = [t for t in slots_a if t in slots_b]
                if shared:
                    violations.append({
                        "alert_id": alert.get("id", ""),
                        "kind": kind,
                        "reason": "interaction_same_slot",
                        "slots": shared,
                        "required_gap_hours": gap,
                        "message": (
                            f"Two interacting medicines are both scheduled at {shared}"
                            + (f" (need {gap:g}h apart)" if gap > 0 else "")
                        ),
                    })
                if gap > 0:
                    for x in slots_a:
                        for y in slots_b:
                            if x == y:
                                continue  # already reported as interaction_same_slot
                            if abs(_minutes(x) - _minutes(y)) < gap * 60:
                                violations.append({
                                    "alert_id": alert.get("id", ""),
                                    "kind": kind,
                                    "reason": "required_gap_violated",
                                    "slots": [x, y],
                                    "required_gap_hours": gap,
                                    "actual_gap_hours": abs(_minutes(x) - _minutes(y)) / 60.0,
                                    "message": (
                                        f"Gap of {abs(_minutes(x) - _minutes(y)) / 60.0:g}h between "
                                        f"{x} and {y} is below the required {gap:g}h"
                                    ),
                                })

    knowledge_base = schedule.get("knowledge_base") or {}
    return {
        "verified_against_rules": not violations,
        "violations": violations,
        "rules_checked": checked_rules,
        "checked_alerts": len([a for a in alerts if len(a.get("med_ids") or []) >= 2]),
        "checked_at_slots": len(slot_ids),
        "knowledge_version": dict(knowledge_base),
    }
