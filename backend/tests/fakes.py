"""In-memory stand-ins for Google Cloud Firestore.

Used by the test-suite (tests/conftest.py) and by scripts/safety_invariant_audit.py,
so both exercise the same fake. It implements only the subset of the Firestore API
this backend uses, and it refuses `collection_group()` on purpose: that call scans
every tenant's documents, which is how the original SOS implementation leaked
across users.
"""

from __future__ import annotations

# ── Medication helper ─────────────────────────────────────────────────────────

def med(med_id: str, brand: str, profile_id: str = "p1", **kwargs) -> dict:
    base = {
        "id": med_id,
        "profile_id": profile_id,
        "brand_name": brand,
        "generic_name": kwargs.pop("generic_name", ""),
        "status": "active",
        "dosage": kwargs.pop("dosage", ""),
        "frequency_raw": kwargs.pop("frequency_raw", ""),
        "frequency_english": kwargs.pop("frequency_english", ""),
        "timing": kwargs.pop("timing", []),
        "instruction": kwargs.pop("instruction", ""),
    }
    base.update(kwargs)
    return base


# ── In-memory Firestore ───────────────────────────────────────────────────────

class Snapshot:
    def __init__(self, ref: DocRef, data: dict | None):
        self.id = ref.id
        self.reference = ref
        self._data = data
        self.exists = data is not None

    def to_dict(self) -> dict:
        return dict(self._data or {})

    def get(self):
        return self

    def set(self, *a, **k):
        return self.reference.set(*a, **k)

    def update(self, *a, **k):
        return self.reference.update(*a, **k)

    def delete(self):
        return self.reference.delete()


class DocRef:
    def __init__(self, store: FakeFirestore, path: tuple[str, ...]):
        self._store = store
        self._path = path

    @property
    def id(self) -> str:
        return self._path[-1]

    def collection(self, name: str) -> CollectionRef:
        return CollectionRef(self._store, (*self._path, name))

    def get(self) -> Snapshot:
        return Snapshot(self, self._store.docs.get(self._path))

    def set(self, data: dict, merge: bool = False) -> None:
        if merge and self._path in self._store.docs:
            self._store.docs[self._path].update(data)
        else:
            self._store.docs[self._path] = dict(data)

    def update(self, data: dict) -> None:
        if self._path not in self._store.docs:
            raise KeyError(f"document {self._path} does not exist")
        self._store.docs[self._path].update(data)

    def delete(self) -> None:
        self._store.docs.pop(self._path, None)

    def to_dict(self) -> dict:
        return dict(self._store.docs.get(self._path) or {})


class Query:
    def __init__(self, store: FakeFirestore, path: tuple[str, ...], filters=None, cap=None):
        self._store = store
        self._path = path
        self._filters = filters or []
        self._cap = cap

    def where(self, field: str, op: str, value) -> Query:
        return Query(self._store, self._path, [*self._filters, (field, op, value)], self._cap)

    def limit(self, count: int) -> Query:
        return Query(self._store, self._path, self._filters, count)

    def _matches(self) -> list[Snapshot]:
        out = []
        for path, data in self._store.docs.items():
            if len(path) != len(self._path) + 1 or path[: len(self._path)] != self._path:
                continue
            ok = True
            for field, op, value in self._filters:
                actual = data.get(field)
                if (op == "==" and actual != value) or (op == "!=" and actual == value):
                    ok = False
            if ok:
                out.append(Snapshot(DocRef(self._store, path), data))
        out.sort(key=lambda s: s.id)
        return out[: self._cap] if self._cap else out

    def stream(self):
        return iter(self._matches())

    def get(self):
        return self._matches()


class CollectionRef:
    def __init__(self, store: FakeFirestore, path: tuple[str, ...]):
        self._store = store
        self._path = path

    def document(self, doc_id: str | None = None) -> DocRef:
        doc_id = doc_id or f"auto{len(self._store.docs)}"
        return DocRef(self._store, (*self._path, doc_id))

    def stream(self):
        return Query(self._store, self._path).stream()

    def get(self):
        return Query(self._store, self._path).get()

    def where(self, field: str, op: str, value) -> Query:
        return Query(self._store, self._path).where(field, op, value)

    def limit(self, count: int) -> Query:
        return Query(self._store, self._path).limit(count)

    def add(self, data: dict) -> tuple[None, DocRef]:
        ref = self.document()
        ref.set(data)
        return None, ref


class WriteBatch:
    def __init__(self, store: FakeFirestore):
        self._store = store
        self._ops = []

    def set(self, ref: DocRef, data: dict, merge: bool = False):
        self._ops.append(("set", ref, data, merge))

    def update(self, ref: DocRef, data: dict):
        self._ops.append(("update", ref, data, False))

    def delete(self, ref: DocRef):
        self._ops.append(("delete", ref, None, False))

    def commit(self):
        for op, ref, data, merge in self._ops:
            if op == "set":
                ref.set(data, merge=merge)
            elif op == "update":
                ref.update(data)
            else:
                ref.delete()
        self._ops.clear()


class FakeFirestore:
    """In-memory Firestore supporting the subset of the API this backend uses."""

    def __init__(self):
        #: full document path -> document data
        self.docs: dict[tuple[str, ...], dict] = {}

    def collection(self, name: str) -> CollectionRef:
        return CollectionRef(self, (name,))

    def batch(self) -> WriteBatch:
        return WriteBatch(self)

    def collection_group(self, name: str):
        # Deliberately unsupported. The previous SOS implementation scanned
        # every user's devices with a collection-group query and then wrote into
        # another user's document - a cross-tenant authorisation bug that tests
        # must catch if it ever comes back.
        raise AssertionError(
            "collection_group() must not be used: it crosses tenant boundaries"
        )


