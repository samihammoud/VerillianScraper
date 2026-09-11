"""Round-trip check: annotate one real pattern, confirm account_patterns
reflects note/hidden/sort_order, then clean up."""
from fastapi.testclient import TestClient
from sqlalchemy import select, delete
from src.main import app
from src.db.models import PatternNote, World
from src.db.session import SessionLocal

db = SessionLocal()
slug = db.execute(select(World.slug)).scalars().first()
c = TestClient(app)

accounts = c.get(f"/worlds/{slug}/accounts").json()
acct = next((a for a in accounts if len(a["patterns"]) >= 2), accounts[0] if accounts else None)
assert acct, "no accounts with patterns — need routed+described posts for this check"
p0, p1 = acct["patterns"][0], acct["patterns"][1] if len(acct["patterns"]) > 1 else (acct["patterns"][0],)*2
print("about:", p0["about"][:160])
assert p0["key"] and p0["note"] == "" and p0["hidden"] is False

assert c.put(f"/worlds/{slug}/patterns/{p0['key']}", json={"note": "hi", "hidden": True}).status_code == 200
assert c.put(f"/worlds/{slug}/patterns/{p1['key']}", json={"sort_order": -1}).status_code == 200

again = next(a for a in c.get(f"/worlds/{slug}/accounts").json() if a["handle"] == acct["handle"])
by_key = {p["key"]: p for p in again["patterns"]}
assert by_key[p0["key"]] == {**by_key[p0["key"]], "note": "hi", "hidden": True}, by_key[p0["key"]]
assert again["patterns"][0]["key"] == p1["key"], "sort_order not applied"
# partial PUT must not clobber the other fields
assert c.put(f"/worlds/{slug}/patterns/{p0['key']}", json={"hidden": False}).json()["note"] == "hi"

db.execute(delete(PatternNote).where(PatternNote.anchor_post_id.in_([p0["key"], p1["key"]])))
db.commit()
print("OK")
