"""Seeds topology.worlds with the 8 hand-written world definitions.

Only worlds gets populated here — world_posts stays empty until routing
(a later phase) inserts rows once real scraped posts are embedded and
compared against worlds.reference_embedding.
"""

import itertools

import numpy as np
from sqlalchemy import select

from src.db.models import World
from src.db.session import SessionLocal
from src.services.crawl_config import load_world
from src.services.embeddings import embed

WORLDS = [
    {
        "slug": "health-wellness",
        "name": "Health & Wellness",
        "description": (
            "Supplements, sleep aids, recovery tools, biohacking routines, wearable health tech, gut "
            "health, energy/focus products. Fitness content included only when framed around health "
            "outcomes (recovery, sleep, longevity) — not pure workout/exercise instruction. Distinct "
            "from Beauty & Skincare, which covers topical/cosmetic products rather than ingestible, "
            "systemic, or wearable ones. Distinct from Food & Cooking: a supplement stack is Health, a "
            "recipe is Food, even if both are 'healthy.'"
        ),
        "example_snippets": [
            "this magnesium glycinate changed my sleep completely",
            "cold plunge routine for recovery day",
            "whoop vs oura ring after 3 months",
            "gut health stack that actually worked for me",
            "morning supplement stack for energy",
        ],
    },
    {
        "slug": "food-cooking",
        "name": "Food & Cooking",
        "description": (
            "Recipes, kitchen gadgets, meal prep, snacks, drinks, restaurant/food reviews. Includes "
            "cooking tools and appliances when the focus is preparing or eating food. Distinct from "
            "Health & Wellness: a recipe or snack is Food even if marketed as healthy; a supplement or "
            "ingestible product sold on a health claim (not as food) is Health. Distinct from Home & "
            "Living: a kitchen gadget used to cook is Food, general kitchen organization/decor is Home."
        ),
        "example_snippets": [
            "5 minute high protein breakfast",
            "this $20 rice cooker is unreal",
            "what I eat in a day as a college student",
            "trying the viral cucumber salad recipe",
            "best iced coffee recipe at home",
        ],
    },
    {
        "slug": "beauty-skincare",
        "name": "Beauty & Skincare",
        "description": (
            "Makeup, skincare routines, haircare, beauty tools and devices (facial rollers, hair tools, "
            "LED masks), nail content. Distinct from Health & Wellness: topical/cosmetic products are "
            "Beauty; ingestible or systemic health products are Health, even if marketed as 'wellness' "
            "(e.g. collagen powder is Health, a jade roller is Beauty). Distinct from Fashion: beauty "
            "covers the face/hair/skin/nails, not clothing or worn accessories."
        ),
        "example_snippets": [
            "grwm using only clean beauty products",
            "this serum cleared my skin in 2 weeks",
            "affordable dupe for the viral foundation",
            "curly hair routine for definition",
            "led mask review after 30 days",
        ],
    },
    {
        "slug": "home-living",
        "name": "Home & Living",
        "description": (
            "Home decor, organization, storage solutions, cleaning products, smart home devices used "
            "for the home itself (not personal gadgets), furniture, small home appliances not centered "
            "on cooking. Distinct from Food & Cooking: kitchen tools used to prepare food are Food, "
            "kitchen storage/organization is Home. Distinct from Tech & Gadgets: a smart home device "
            "(thermostat, robot vacuum) is Home when framed around the living space; a personal gadget "
            "(phone accessory, wearable, app) is Tech."
        ),
        "example_snippets": [
            "small apartment organization hacks",
            "this robot vacuum saved my life",
            "amazon finds for a cozy living room",
            "closet organization system that actually works",
            "cleaning products that changed my routine",
        ],
    },
    {
        "slug": "fashion-accessories",
        "name": "Fashion & Accessories",
        "description": (
            "Clothing, jewelry, bags, shoes, styling content, outfit try-ons, worn accessories. Distinct "
            "from Beauty & Skincare: fashion is worn items on the body, beauty is applied to face/hair/skin. "
            "Distinct from Tech & Gadgets: a smartwatch worn primarily as jewelry/style is borderline — "
            "default to Fashion if the post frames it as a style choice, Tech if framed around function/"
            "features."
        ),
        "example_snippets": [
            "outfit ideas for a night out",
            "this bag is worth the splurge",
            "thrifted this jacket for $8",
            "gold jewelry stacking guide",
            "shoes that go with everything",
        ],
    },
    {
        "slug": "tech-gadgets",
        "name": "Tech & Gadgets",
        "description": (
            "Electronics, apps, personal tech gear, productivity tools, phone accessories, wearables "
            "framed around function. Distinct from Home & Living: a smart device is Tech when it's "
            "personal/functional (headphones, a productivity gadget), Home when it's about the living "
            "space itself. Distinct from Fashion: a wearable is Tech when the post is about features/specs, "
            "Fashion when it's about how it looks."
        ),
        "example_snippets": [
            "productivity apps that changed how I work",
            "unboxing the new earbuds",
            "hidden iphone settings you need to know",
            "best budget laptop for students",
            "gadgets under $30 that are actually useful",
        ],
    },
    {
        "slug": "pets",
        "name": "Pets",
        "description": (
            "Pet products, food, toys, accessories, grooming gear, pet furniture. Anything purchased for "
            "or used on a pet. Distinct from every other world by subject (the pet), not by product "
            "category — a bed is Pets if it's for a dog, Home if it's for a human."
        ),
        "example_snippets": [
            "my dog's favorite toy right now",
            "switched my cat to this food and the difference is wild",
            "best harness for a puller",
            "diy pet bed that took 10 minutes",
            "grooming kit that saved me money",
        ],
    },
    {
        "slug": "parenting-baby",
        "name": "Parenting & Baby",
        "description": (
            "Baby gear, toys, feeding products, nursery items, parenting products and routines. Distinct "
            "from Pets by subject (a human child, not an animal). Distinct from Home & Living: nursery "
            "decor/organization is Parenting & Baby when framed around the child, Home when it's general "
            "household organization."
        ),
        "example_snippets": [
            "must have items for a newborn",
            "toddler toys that actually keep them busy",
            "nursery setup on a budget",
            "baby led weaning first foods",
            "stroller comparison after 6 months of use",
        ],
    },
    load_world("romance"),
]


def seed_worlds(db) -> None:
    """Additive: skips any slug already present. Appending a ninth world and
    re-running must never touch the existing eight — their world_posts rows
    and UUIDs stay exactly as they are. See reseed-worlds for the deliberate,
    destructive full-truncate path."""
    existing = set(db.execute(select(World.slug)).scalars().all())
    for w in WORLDS:
        if w["slug"] in existing:
            continue
        embed_input = w["description"] + "\n" + "\n".join(w["example_snippets"])
        vec = embed(embed_input)
        db.add(
            World(
                slug=w["slug"],
                name=w["name"],
                description=w["description"],
                example_snippets=w["example_snippets"],
                reference_embedding=vec,
            )
        )
    db.commit()


def cosine_similarity(a: list[float], b: list[float]) -> float:
    a_arr, b_arr = np.array(a), np.array(b)
    return float(np.dot(a_arr, b_arr) / (np.linalg.norm(a_arr) * np.linalg.norm(b_arr)))


def print_similarity_matrix(db, threshold: float = 0.8) -> None:
    worlds = db.execute(select(World).order_by(World.slug)).scalars().all()

    header = " " * 22 + "".join(f"{w.slug[:18]:>20}" for w in worlds)
    print(header)
    for row_world in worlds:
        row = f"{row_world.slug[:20]:<22}"
        for col_world in worlds:
            sim = cosine_similarity(row_world.reference_embedding, col_world.reference_embedding)
            row += f"{sim:>20.4f}"
        print(row)

    print(f"\nPairs above {threshold}:")
    flagged = False
    for w1, w2 in itertools.combinations(worlds, 2):
        sim = cosine_similarity(w1.reference_embedding, w2.reference_embedding)
        if sim > threshold:
            flagged = True
            print(f"  {w1.slug} <-> {w2.slug}: {sim:.4f}")
    if not flagged:
        print("  none")


if __name__ == "__main__":
    db = SessionLocal()
    try:
        before = set(db.execute(select(World.slug)).scalars().all())
        seed_worlds(db)
        after = set(db.execute(select(World.slug)).scalars().all())
        added = after - before
        print(f"Added {len(added)} world(s): {sorted(added) or 'none'}. Total: {len(after)}.")

        print_similarity_matrix(db)
    finally:
        db.close()
