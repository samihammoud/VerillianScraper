"""Round-trip check for the shared annotation store (topology.annotations).

The thing that breaks silently: set_annotation is a merge-patch, so a one-field
PUT must not reset the other four. Needs the local Postgres up.
"""

from src.db.models import ANNOTATION_FIELDS, Annotation
from src.db.session import SessionLocal
from src.routes.worlds import set_annotation, term_key


def test_annotation_patch_is_a_merge():
    key = term_key("test-world", "product", "a term: with/odd chars")
    db = SessionLocal()
    try:
        db.query(Annotation).filter(Annotation.key == key).delete()
        db.commit()

        assert set(ANNOTATION_FIELDS) == {"note", "favorite", "reviewed", "hidden", "sort_order"}

        created = set_annotation(key, {"favorite": True, "note": "keep me"})
        assert created["favorite"] is True and created["note"] == "keep me"

        # one-field patch: everything else survives
        patched = set_annotation(key, {"reviewed": True})
        assert patched["reviewed"] is True
        assert patched["favorite"] is True, "merge-patch clobbered an untouched field"
        assert patched["note"] == "keep me"
        assert patched["sort_order"] == 0

        assert set_annotation(key, {"favorite": False})["favorite"] is False
    finally:
        db.query(Annotation).filter(Annotation.key == key).delete()
        db.commit()
        db.close()


if __name__ == "__main__":
    test_annotation_patch_is_a_merge()
    print("annotation round-trip ok")
