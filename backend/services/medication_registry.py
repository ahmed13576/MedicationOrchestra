"""
medication_registry.py — Medication Orchestra

Exact, auditable drug identity resolution. This module exists because the
previous implementation resolved "which drug is this?" with substring
containment, which conflates:

    cortisone      vs  hydrocortisone
    ampicillin     vs  pivampicillin
    codeine        vs  dihydrocodeine

...and silently fails to match anything whose wording differs from the corpus
("Aspirin (Acetylsalicylic Acid)" vs "aspirin").

Design rules (do not relax these):

1. Identity is resolved by EXACT lookup in a normalised synonym table. There is
   no `in`, no `startswith`, no fuzzy score, and no edit distance anywhere in
   the match path.
2. A brand is resolved to a *set* of ingredients with strengths - never to an
   opaque string. Combination products are therefore first-class.
3. Every resolution carries provenance: how it was resolved (`resolved_by`),
   the knowledge-base versions, and the raw input. The API surfaces this so a
   pharmacist can audit any alert back to a source.
4. If an ingredient cannot be resolved, it is reported as UNRESOLVED. It is
   never silently dropped, and it never silently becomes "safe".

Knowledge-base versions are part of every cache key, so correcting a mapping
invalidates stale clinical answers.
"""

from __future__ import annotations

import csv
import itertools
import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "knowledge"

# ── Knowledge-base version identifiers (surfaced in every API response) ───────
INGREDIENTS_FILE = KNOWLEDGE_DIR / "ingredients.json"
INTERACTIONS_FILE = KNOWLEDGE_DIR / "interactions.json"
BRANDS_FILE = KNOWLEDGE_DIR / "brand_mapping.csv"


class KnowledgeBaseError(RuntimeError):
    """Raised when the clinical knowledge base is missing or unloadable.

    This MUST be fatal at startup. Serving a medication-safety response from a
    partially-loaded knowledge base is exactly the failure mode this product
    cannot have (see docs/ADVERSARIAL_REVIEW.md, F-01/F-02).
    """


# ── Normalisation ─────────────────────────────────────────────────────────────

#: Words that carry no identifying information in an Indian brand or salt name.
_STOPWORDS = {
    "tablet", "tab", "tabs", "capsule", "cap", "caps", "syrup", "syp", "susp",
    "suspension", "injection", "inj", "gel", "cream", "ointment", "drops", "drop",
    "inhaler", "respules", "sachet", "powder", "solution", "sr", "xr", "er", "cr",
    "mr", "ds", "forte", "plus", "ip", "bp", "usp", "nf", "pro",
    "kit", "sachets", "mg", "mcg", "g", "ml", "iu", "i", "u", "of",
}

_UNIT_STRIP_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|g|gm|ml|iu|units?|%)\b", re.IGNORECASE
)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9\s]+")
_WHITESPACE_RE = re.compile(r"\s+")
#: Numeric pack descriptors: "650", "1x10", "10's", "2x5"
_PACK_QTY_RE = re.compile(r"^\d+\s*(?:x\s*\d+)?\s*(?:'s|s)?$", re.IGNORECASE)


def normalise_name(value: str | None) -> str:
    """Normalise a brand/salt string for exact-key comparison.

    Purely lexical: lowercase, strip units and pack quantities, drop
    non-alphanumerics and filler words. It never compares two different names
    against each other, so it cannot invent a match.
    """
    if not value or not isinstance(value, str):
        return ""
    text = value.lower().strip()
    text = text.replace("&", " and ").replace("+", " and ")
    text = _UNIT_STRIP_RE.sub(" ", text)
    text = _NON_ALNUM_RE.sub(" ", text)
    tokens = [
        t for t in _WHITESPACE_RE.split(text)
        if t and t not in _STOPWORDS and not _PACK_QTY_RE.match(t)
    ]
    return " ".join(tokens)


@lru_cache(maxsize=1)
def _stop_words() -> frozenset:
    return frozenset(_STOPWORDS)


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class IngredientAmount:
    """A single active ingredient with its strength, where known."""
    ingredient_id: str
    name: str
    mg: float | None = None
    mg_known: bool = False

    def to_dict(self) -> dict:
        return {
            "ingredient_id": self.ingredient_id,
            "name": self.name,
            "strength_mg": self.mg if self.mg_known else None,
            "strength_known": self.mg_known,
        }


@dataclass
class ResolvedMedication:
    """The result of resolving one medication entry to known ingredients.

    `unresolved` is the important field: it lists the parts of the input that
    the registry could not identify. Callers MUST surface these to the user
    rather than treating the medication as checked-and-clear.
    """
    brand_name: str
    generic_name: str
    ingredients: list[IngredientAmount] = field(default_factory=list)
    #: Active-ingredient text we could not identify. Anything here means the
    #: medication has NOT been fully checked and must be reported as such.
    unresolved: list[str] = field(default_factory=list)
    #: Labels (usually brand names) absent from the brand table, where the active
    #: ingredients were nevertheless established from the composition text.
    #: Informational only - these do not make a medication "unchecked".
    unrecognised_labels: list[str] = field(default_factory=list)
    resolved_by: str = "unresolved"
    confidence: str = "low"
    kb_versions: dict = field(default_factory=dict)

    @property
    def ingredient_ids(self) -> set[str]:
        return {i.ingredient_id for i in self.ingredients}

    @property
    def is_fully_resolved(self) -> bool:
        return bool(self.ingredients) and not self.unresolved

    @property
    def is_partially_resolved(self) -> bool:
        """Some ingredients identified, some passed through unresolved."""
        return bool(self.ingredients) and bool(self.unresolved)

    def to_dict(self) -> dict:
        return {
            "brand_name": self.brand_name,
            "generic_name": self.generic_name,
            "ingredients": [i.to_dict() for i in self.ingredients],
            "unresolved": list(self.unresolved),
            "unrecognised_labels": list(self.unrecognised_labels),
            "resolved_by": self.resolved_by,
            "confidence": self.confidence,
            "kb_versions": dict(self.kb_versions),
        }


# ── Registry ──────────────────────────────────────────────────────────────────

class MedicationRegistry:
    """Loads the ingredient, interaction and brand knowledge bases.

    Fails hard (raises) on a missing or malformed knowledge base. A soft failure
    here is how a product ends up telling a patient "no interactions found" with
    an empty database.
    """

    MIN_INGREDIENTS = 50
    MIN_INTERACTION_RULES = 10

    def __init__(
        self,
        ingredients_file: Path = INGREDIENTS_FILE,
        interactions_file: Path = INTERACTIONS_FILE,
        brands_file: Path = BRANDS_FILE,
    ) -> None:
        self._ingredients_file = Path(ingredients_file)
        self._interactions_file = Path(interactions_file)
        self._brands_file = Path(brands_file)

        self.ingredients: dict[str, dict] = {}
        self.synonym_index: dict[str, str] = {}
        self.rules: list[dict] = []
        self.advisories: list[dict] = []
        self.brand_index: dict[str, list[dict]] = {}
        self.ceilings: dict[str, dict] = {}
        self.sources: dict[str, str] = {}
        self.versions: dict[str, str] = {}
        self.review_status: str = "unknown"
        self._pair_index: dict[frozenset, list[dict]] = {}
        #: ingredient_id -> ids of rules/advisories mentioning it (rule id list)
        self._ingredient_rule_index: dict[str, list[dict]] = {}

        self._load()

    # -- loading ------------------------------------------------------------

    def _read_json(self, path: Path, what: str) -> dict:
        if not path.exists():
            raise KnowledgeBaseError(
                f"{what} knowledge base not found at {path}. The service refuses to "
                f"start without it: an answer computed from an empty knowledge base is "
                f"a false 'no interactions found'."
            )
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except json.JSONDecodeError as exc:
            raise KnowledgeBaseError(f"{what} knowledge base at {path} is not valid JSON: {exc}") from exc

    def _load(self) -> None:
        ing_doc = self._read_json(self._ingredients_file, "ingredient")
        self.versions["ingredients"] = ing_doc.get("version", "unknown")
        raw_ingredients = ing_doc.get("ingredients") or {}
        if len(raw_ingredients) < self.MIN_INGREDIENTS:
            raise KnowledgeBaseError(
                f"ingredient registry has only {len(raw_ingredients)} entries "
                f"(minimum {self.MIN_INGREDIENTS}) — refusing to start"
            )
        self.ingredients = raw_ingredients

        for ingredient_id, entry in self.ingredients.items():
            keys = {normalise_name(ingredient_id), normalise_name(entry.get("name", ""))}
            keys.update(normalise_name(s) for s in entry.get("synonyms", []))
            keys.discard("")
            for key in keys:
                existing = self.synonym_index.get(key)
                if existing is not None and existing != ingredient_id:
                    # Two different ingredients claiming the same synonym is a data
                    # bug that would silently mis-identify a drug. Fail loudly.
                    raise KnowledgeBaseError(
                        f"synonym collision: '{key}' maps to both '{existing}' and "
                        f"'{ingredient_id}'"
                    )
                self.synonym_index[key] = ingredient_id

        int_doc = self._read_json(self._interactions_file, "interaction")
        self.versions["interactions"] = int_doc.get("version", "unknown")
        self.review_status = int_doc.get("review_status", "unknown")
        self.sources = int_doc.get("sources", {})
        self.rules = int_doc.get("rules") or []
        self.advisories = int_doc.get("advisories") or []
        self.ceilings = int_doc.get("duplicate_ingredient_ceilings") or {}

        if len(self.rules) < self.MIN_INTERACTION_RULES:
            raise KnowledgeBaseError(
                f"interaction knowledge base has only {len(self.rules)} rules "
                f"(minimum {self.MIN_INTERACTION_RULES}) — refusing to start"
            )

        for rule in self.rules:
            ids = [self._require_ingredient(i, rule["id"]) for i in rule["ingredients"]]
            if len(ids) < 2:
                raise KnowledgeBaseError(f"rule {rule['id']} must list at least two ingredients")
            rule["_ingredients"] = ids
            rule["_source_label"] = self.sources.get(rule.get("source", ""), rule.get("source", ""))
            for iid in ids:
                self._ingredient_rule_index.setdefault(iid, []).append(rule)
            # Mechanism groups: an alert may only fire when the patient's
            # ingredients span TWO DIFFERENT groups. Without this, a rule that
            # lists several NSAIDs fires for a patient taking two different
            # NSAIDs, and the alert reads as if an SSRI were involved.
            #
            # A rule without groups is a knowledge-base defect, not something to
            # paper over at runtime: it would either silence the rule or fire it
            # on the wrong patients, so the service refuses to start.
            raw_groups = rule.get("groups") or []
            if len(raw_groups) < 2:
                raise KnowledgeBaseError(
                    f"rule {rule['id']} must declare at least two mechanism groups "
                    f"(got {len(raw_groups)}); without them the rule cannot tell "
                    f"which ingredients interact and would fire on the wrong patients"
                )
            groups = [
                [self._require_ingredient(i, rule["id"]) for i in group]
                for group in raw_groups
            ]
            seen_groups: set[str] = set()
            for group in groups:
                overlap = seen_groups & set(group)
                if overlap:
                    raise KnowledgeBaseError(
                        f"rule {rule['id']} lists {sorted(overlap)} in more than one "
                        f"mechanism group, so it would fire on a single ingredient"
                    )
                seen_groups |= set(group)
            ungrouped = set(ids) - seen_groups
            if ungrouped:
                raise KnowledgeBaseError(
                    f"rule {rule['id']} lists {sorted(ungrouped)} in 'ingredients' but "
                    f"not in any mechanism group, so those pairs can never alert"
                )
            rule["_groups"] = groups

            # Every cross-group pair is a match candidate. Two ingredients in the
            # same group are the same kind of medicine, so pairing them would
            # fire this rule on a patient who takes both of them and no medicine
            # from the other group at all.
            for a_idx, group in enumerate(groups):
                for other in groups[a_idx + 1:]:
                    for a in group:
                        for b in other:
                            self._pair_index.setdefault(frozenset((a, b)), []).append(rule)

        for adv in self.advisories:
            ids = [self._require_ingredient(i, adv["id"]) for i in adv["ingredients"]]
            adv["_ingredients"] = ids
            adv["_source_label"] = self.sources.get(adv.get("source", ""), adv.get("source", ""))
            for iid in ids:
                self._ingredient_rule_index.setdefault(iid, []).append(adv)

        self._load_brands()
        self._build_mechanism_groups()

        logger.info(
            "Knowledge base loaded: %d ingredients, %d interaction rules, %d advisories, "
            "%d brand presentations (ingredients v%s, interactions v%s, review=%s)",
            len(self.ingredients), len(self.rules), len(self.advisories),
            sum(len(v) for v in self.brand_index.values()),
            self.versions["ingredients"], self.versions["interactions"], self.review_status,
        )

    # ── Mechanism-group index (S-4) ──────────────────────────────────────────
    # Union-find over the `groups` declared in each interaction rule. Two
    # ingredients placed in the SAME group of any rule share a mechanism of
    # action (e.g. ibuprofen and diclofenac are both NSAIDs). The interaction
    # path only fires across two DIFFERENT groups, so without this index the
    # runtime never tells a patient that two different-ingredient products are
    # the same type of medicine — the most easily missed duplication. Severity
    # stays `moderate` until a clinician confirms the group boundaries.

    def _build_mechanism_groups(self) -> None:
        parent: dict[str, str] = {}

        def find(x: str) -> str:
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: str, b: str) -> None:
            ra, rb = find(a), find(b)
            if ra == rb:
                return
            # Deterministic root keeps ids stable across runs.
            root, other = (ra, rb) if ra <= rb else (rb, ra)
            parent[other] = root

        # (group members, source label, rule id) for every group with >=2 members.
        raw: list[tuple[list[str], str, str]] = []
        for rule in self.rules:
            src = rule.get("_source_label") or rule.get("source", "")
            for group in rule["_groups"]:
                if len(group) < 2:
                    continue
                raw.append((list(group), src, rule["id"]))
                for a, b in itertools.combinations(sorted(group), 2):
                    union(a, b)

        sources: dict[str, set[str]] = defaultdict(set)
        rule_ids: dict[str, list[str]] = defaultdict(list)
        members: dict[str, set[str]] = defaultdict(set)
        for group, src, rid in raw:
            root = find(group[0])
            sources[root].add(src)
            rule_ids[root].append(rid)
            for iid in group:
                members[find(iid)].add(iid)

        self._mech_parent = parent
        self._mech_sources = sources
        self._mech_rule_ids = rule_ids
        self._mech_members = members

    def _find_mech(self, x: str) -> str:
        parent = self._mech_parent
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def mechanism_group_of(self, ingredient_id: str) -> str | None:
        """Cluster root for an ingredient that belongs to a multi-member
        mechanism group, or ``None`` when the ingredient is not in any group of
        size >= 2 (so it can never be part of a duplicate-therapy finding)."""
        if ingredient_id not in self._mech_parent:
            return None
        return self._find_mech(ingredient_id)

    def mechanism_cluster_info(self, root: str) -> dict:
        return {
            "sources": sorted(self._mech_sources.get(root, set())),
            "rule_ids": sorted(set(self._mech_rule_ids.get(root, []))),
            "ingredients": sorted(self._mech_members.get(root, set())),
        }

    def _require_ingredient(self, ingredient_id: str, owner: str) -> str:
        if ingredient_id not in self.ingredients:
            raise KnowledgeBaseError(
                f"{owner} references unknown ingredient '{ingredient_id}'"
            )
        return ingredient_id

    def _load_brands(self) -> None:
        if not self._brands_file.exists():
            raise KnowledgeBaseError(f"brand mapping not found at {self._brands_file}")
        count = 0
        with open(self._brands_file, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                brand = (row.get("brand_name") or "").strip()
                if not brand:
                    continue
                compositions = []
                for chunk in (row.get("pack_ingredients") or "").split(";"):
                    chunk = chunk.strip()
                    if not chunk:
                        continue
                    name, _, strength = chunk.partition(":")
                    iid = self.synonym_index.get(normalise_name(name))
                    if iid is None:
                        raise KnowledgeBaseError(
                            f"brand '{brand}' references unknown ingredient '{name}'"
                        )
                    try:
                        mg = float(strength) if strength.strip() else None
                    except ValueError:
                        mg = None
                    compositions.append((iid, mg))
                key = normalise_name(brand)
                if not key or not compositions:
                    continue
                self.brand_index.setdefault(key, []).append({
                    "brand_name": brand,
                    "strength_label": (row.get("strength") or "").strip(),
                    "ingredients": compositions,
                })
                count += 1
        if count == 0:
            raise KnowledgeBaseError("brand mapping is empty — refusing to start")

    # -- resolution ---------------------------------------------------------

    def resolve_ingredient(self, text: str | None) -> str | None:
        """Exact synonym lookup. Returns an ingredient id or None. Never fuzzy."""
        key = normalise_name(text)
        if not key:
            return None
        return self.synonym_index.get(key)

    def resolve_brand(self, brand_name: str | None) -> list[dict]:
        """Resolve a brand to its presentations (base name match, shortest first)."""
        key = normalise_name(brand_name)
        if not key:
            return []
        exact = self.brand_index.get(key)
        if exact:
            return exact
        # Try dropping trailing flavour tokens one at a time ("pan d 40mg" -> "pan d" -> "pan"),
        # always by exact key lookup - never by substring containment.
        tokens = key.split()
        for cut in range(len(tokens) - 1, 0, -1):
            candidate = " ".join(tokens[:cut])
            hits = self.brand_index.get(candidate)
            if hits:
                return hits
        return []

    #: Splits a composition string into candidate ingredient names.
    _COMPONENT_SPLIT_RE = re.compile(r"[+/,;()]|\band\b|\bwith\b|\bof\b", re.IGNORECASE)

    def _components(self, text: str) -> list[str]:
        """Break a composition string into candidate ingredient names.

        Handles the shapes that actually appear on Indian labels and in model
        output: "Ibuprofen + Paracetamol", "Aspirin (Acetylsalicylic Acid)
        75mg", "Amoxicillin and Clavulanic Acid", "Metformin Hydrochloride IP".
        """
        if not text or not isinstance(text, str):
            return []
        parts = []
        for chunk in self._COMPONENT_SPLIT_RE.split(text):
            chunk = (chunk or "").strip(" .:-")
            if not chunk:
                continue
            # A chunk that is only a strength ("650mg") carries no name.
            if re.fullmatch(r"[\d\s.x']*(?:mg|mcg|g|ml|iu)?[\d\s.x']*", chunk, re.IGNORECASE):
                continue
            parts.append(chunk)
        return parts

    def _parse_free_text(self, text: str) -> list[tuple[str, float | None]]:
        """Resolve a free-text composition string into (ingredient_id, mg) pairs.

        Any part that does not resolve EXACTLY is skipped here and reported by
        the caller as unresolved - never guessed, never silently dropped.
        """
        found: list[tuple[str, float | None]] = []
        for part in self._components(text):
            mg: float | None = None
            match = re.search(r"(\d+(?:\.\d+)?)\s*(mg|mcg|g)\b", part, re.IGNORECASE)
            if match:
                value = float(match.group(1))
                unit = match.group(2).lower()
                mg = value / 1000.0 if unit == "mcg" else value * 1000.0 if unit == "g" else value
            iid = self.resolve_ingredient(part)
            if iid:
                found.append((iid, mg))
        # A strength written outside the name, e.g. "Paracetamol + Ibuprofen 400mg"
        trailing = re.search(r"(\d+(?:\.\d+)?)\s*(mg|mcg|g)\b", text or "", re.IGNORECASE)
        if trailing and found and all(mg is None for _, mg in found):
            value = float(trailing.group(1))
            unit = trailing.group(2).lower()
            mg = value / 1000.0 if unit == "mcg" else value * 1000.0 if unit == "g" else value
            found = [(iid, mg) for iid, _ in found]
        return found

    def resolve_medication(
        self, brand_name: str | None, generic_name: str | None = None
    ) -> ResolvedMedication:
        """Resolve one medication entry to ingredients, with provenance.

        Resolution order:
          1. Brand presentation table (authoritative for Indian brands).
          2. Free-text generic composition ("Ibuprofen + Paracetamol").
          3. Exact single-ingredient synonym (brand field itself is a salt name).
        Anything left over is reported in `unresolved`.
        """
        brand = (brand_name or "").strip()
        generic = (generic_name or "").strip()
        result = ResolvedMedication(brand_name=brand, generic_name=generic)

        amounts: dict[str, IngredientAmount] = {}
        unresolved: list[str] = []
        unrecognised_labels: list[str] = []

        presentations = self.resolve_brand(brand)
        if presentations:
            presentation = presentations[0]
            for iid, mg in presentation["ingredients"]:
                amounts[iid] = IngredientAmount(
                    ingredient_id=iid,
                    name=self.ingredients[iid]["name"],
                    mg=mg,
                    mg_known=mg is not None,
                )
            result.resolved_by = "brand_table"
            result.confidence = "high"
            if len(presentations) > 1 and presentations[1]["strength_label"] != presentation["strength_label"]:
                # Same brand, several strengths: flag for human confirmation.
                result.confidence = "medium"
        else:
            # Free-text generic
            for iid, mg in self._parse_free_text(generic):
                amounts.setdefault(iid, IngredientAmount(
                    ingredient_id=iid,
                    name=self.ingredients[iid]["name"],
                    mg=mg,
                    mg_known=mg is not None,
                ))
            if amounts:
                result.resolved_by = "generic_text"
                result.confidence = "medium"

            # Brand field itself may be a salt name (e.g. "Warfarin")
            if not amounts:
                iid = self.resolve_ingredient(brand)
                if iid:
                    amounts[iid] = IngredientAmount(
                        ingredient_id=iid, name=self.ingredients[iid]["name"]
                    )
                    result.resolved_by = "brand_as_salt"
                    result.confidence = "medium"

            # Generic field may itself be a brand name the model recognised
            if not amounts and generic:
                presentations = self.resolve_brand(generic)
                if presentations:
                    for iid, mg in presentations[0]["ingredients"]:
                        amounts[iid] = IngredientAmount(
                            ingredient_id=iid,
                            name=self.ingredients[iid]["name"],
                            mg=mg,
                            mg_known=mg is not None,
                        )
                    result.resolved_by = "generic_as_brand"
                    result.confidence = "medium"

        # Record what we could not identify, so nothing is silently ignored.
        # Two distinct cases, deliberately kept apart:
        #   * active-ingredient text that resolved to nothing -> `unresolved`,
        #     which makes the medication "not fully checked"; and
        #   * a brand label missing from the table while the composition IS known
        #     -> `unrecognised_labels`, informational only. Treating this as
        #     "unchecked" would raise a false alarm for every new brand name and
        #     train the caregiver to ignore the coverage banner.
        for part in self._components(generic):
            if (
                self.resolve_ingredient(part) is None
                and not self.resolve_brand(part)
                and normalise_name(part) not in {normalise_name(x) for x in unresolved}
            ):
                unresolved.append(part)
        for label in (brand, generic):
            if (
                label
                and not self.resolve_brand(label)
                and self.resolve_ingredient(label) is None
                and label not in unrecognised_labels
            ):
                unrecognised_labels.append(label)

        if not amounts:
            # Nothing identified at all: the whole entry is unresolved.
            for label in (brand, generic):
                if label and label not in unresolved:
                    unresolved.append(label)

        result.ingredients = list(amounts.values())
        result.unresolved = unresolved
        result.unrecognised_labels = unrecognised_labels
        result.kb_versions = dict(self.versions)
        if not result.ingredients:
            result.confidence = "low"
            result.resolved_by = "unresolved"
        return result

    # -- queries ------------------------------------------------------------

    def rules_for_pair(self, a: str, b: str) -> list[dict]:
        """Curated rules that `a` and `b` trigger together.

        Only pairs that span two different mechanism groups appear here, so two
        NSAIDs never trigger the SSRI+NSAID rule, and one rule never fires on a
        single ingredient present twice.
        """
        if not a or not b or a == b:
            return []
        return list(self._pair_index.get(frozenset((a, b)), []))

    @staticmethod
    def group_of(rule: dict, ingredient_id: str) -> int:
        """Which mechanism group `ingredient_id` belongs to within `rule`."""
        for index, group in enumerate(rule.get("_groups") or []):
            if ingredient_id in group:
                return index
        return -1

    def advisories_for(self, ingredient_id: str) -> list[dict]:
        return [r for r in self.advisories if ingredient_id in r["_ingredients"]]

    def ceiling_for(self, ingredient_id: str) -> dict | None:
        return self.ceilings.get(ingredient_id)

    @property
    def ingredient_count(self) -> int:
        return len(self.ingredients)

    @property
    def rule_count(self) -> int:
        return len(self.rules)

    def describe(self) -> dict:
        return {
            "versions": dict(self.versions),
            "review_status": self.review_status,
            "ingredients": len(self.ingredients),
            "interaction_rules": len(self.rules),
            "advisories": len(self.advisories),
            "brand_presentations": sum(len(v) for v in self.brand_index.values()),
            "sources": dict(self.sources),
        }


# ── Module-level singleton ────────────────────────────────────────────────────

_registry: MedicationRegistry | None = None


def get_registry(reload: bool = False) -> MedicationRegistry:
    """Return the process-wide registry, loading it on first use.

    Raises KnowledgeBaseError if the knowledge base is missing or below the
    minimum size — callers must not catch this to "continue gracefully".
    """
    global _registry
    if _registry is None or reload:
        _registry = MedicationRegistry()
    return _registry
