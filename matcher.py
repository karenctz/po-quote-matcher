"""Line-item matching and comparison between any two of: a Cactoz quote, a
customer's signed copy/PO, or a supplier PO."""
import difflib

MATCH_THRESHOLD = 0.35
PRICE_TOLERANCE = 0.01


def _similarity(a, b):
    a = (a or "").lower().strip()
    b = (b or "").lower().strip()
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _norm_part_no(part_no):
    return (part_no or "").strip().upper()


def doc_pair_score(items_a, items_b):
    """Rough compatibility score between two whole documents, for auto-suggesting pairs."""
    if not items_a or not items_b:
        return 0.0
    best_scores = []
    for ia in items_a:
        best = max((_similarity(ia["description"], ib["description"]) for ib in items_b), default=0.0)
        best_scores.append(best)
    return sum(best_scores) / len(best_scores)


def compare_line_items(items_a, items_b, mode="margin"):
    """Greedily pairs items_a lines with items_b lines.

    A shared, non-empty part number (case/whitespace-insensitive) is treated
    as an authoritative match, taking priority over description similarity -
    two documents commonly phrase the same part differently (abbreviations,
    added detail, different word order), whereas a matching part number
    reliably identifies the same item. Items without a part number, or with
    one that doesn't match anything on the other side, still fall back to
    the description-similarity matching below.

    mode="margin": items_a is the sell side (Cactoz quote or customer doc) and
    items_b is the buy/cost side (a supplier PO); flags when margin
    (a.unit_price - b.unit_price) <= 0.

    mode="exact_match": both sides are sell-side documents expected to carry
    the same price (e.g. a Cactoz quote vs. the customer's signed copy);
    flags any unit-price difference beyond a small tolerance instead.

    Returns matched pairs (with flags) plus lists of items only found on one side.
    """
    remaining_b = list(enumerate(items_b))
    matched = []
    unmatched_a = []

    for a_item in items_a:
        best_j, best_score, part_no_matched = None, 0.0, False
        a_part = _norm_part_no(a_item.get("part_no"))

        if a_part:
            for j, (_, b_item) in enumerate(remaining_b):
                if _norm_part_no(b_item.get("part_no")) == a_part:
                    best_j, part_no_matched = j, True
                    break

        if best_j is None:
            for j, (_, b_item) in enumerate(remaining_b):
                score = _similarity(a_item["description"], b_item["description"])
                if score > best_score:
                    best_score, best_j = score, j

        if best_j is not None and (part_no_matched or best_score >= MATCH_THRESHOLD):
            _, b_item = remaining_b.pop(best_j)
            desc_score = _similarity(a_item["description"], b_item["description"])
            qty_match = (
                a_item.get("qty") is not None
                and b_item.get("qty") is not None
                and float(a_item["qty"]) == float(b_item["qty"])
            )
            margin = None
            margin_flag = None
            price_mismatch = None
            if a_item.get("unit_price") is not None and b_item.get("unit_price") is not None:
                margin = a_item["unit_price"] - b_item["unit_price"]
                if mode == "margin":
                    margin_flag = margin <= 0
                else:
                    price_mismatch = abs(margin) > PRICE_TOLERANCE
            matched.append({
                "item_a": a_item,
                "item_b": b_item,
                "similarity": round(desc_score, 2),
                "part_no_matched": part_no_matched,
                "qty_match": qty_match,
                "margin": margin,
                "margin_flag": margin_flag,
                "price_mismatch": price_mismatch,
            })
        else:
            unmatched_a.append(a_item)

    unmatched_b = [b_item for _, b_item in remaining_b]
    return {
        "matched": matched,
        "unmatched_a": unmatched_a,
        "unmatched_b": unmatched_b,
        "mode": mode,
    }


def verdict(result):
    issues = []
    mode = result.get("mode", "margin")
    for m in result["matched"]:
        desc = m["item_a"]["description"][:50]
        if not m["qty_match"]:
            issues.append(f"Qty mismatch on \"{desc}\": {m['item_a'].get('qty')} vs {m['item_b'].get('qty')}")
        if mode == "margin" and m["margin_flag"]:
            issues.append(f"Zero/negative margin on \"{desc}\": buy {m['item_b'].get('unit_price')} vs sell {m['item_a'].get('unit_price')}")
        if mode == "exact_match" and m["price_mismatch"]:
            issues.append(f"Price mismatch on \"{desc}\": {m['item_a'].get('unit_price')} vs {m['item_b'].get('unit_price')}")
        if not m["part_no_matched"] and m["similarity"] < 0.6:
            issues.append(f"Weak description match (similarity {m['similarity']}) on \"{desc}\"")
    for item in result["unmatched_a"]:
        issues.append(f"Not found on the other side: \"{item['description'][:60]}\"")
    for item in result["unmatched_b"]:
        issues.append(f"Not found on the other side: \"{item['description'][:60]}\"")
    return issues
