"""Line-item matching and comparison between a supplier PO and a customer quote/PO."""
import difflib

MATCH_THRESHOLD = 0.35


def _similarity(a, b):
    a = (a or "").lower().strip()
    b = (b or "").lower().strip()
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def doc_pair_score(items_a, items_b):
    """Rough compatibility score between two whole documents, for auto-suggesting pairs."""
    if not items_a or not items_b:
        return 0.0
    best_scores = []
    for ia in items_a:
        best = max((_similarity(ia["description"], ib["description"]) for ib in items_b), default=0.0)
        best_scores.append(best)
    return sum(best_scores) / len(best_scores)


def compare_line_items(supplier_items, customer_items):
    """Greedily pairs supplier PO lines with customer doc lines by description similarity.

    Returns matched pairs (with flags) plus lists of items only found on one side.
    """
    remaining_customer = list(enumerate(customer_items))
    matched = []
    unmatched_supplier = []

    for s_idx, s_item in enumerate(supplier_items):
        best_j, best_score = None, 0.0
        for j, (c_idx, c_item) in enumerate(remaining_customer):
            score = _similarity(s_item["description"], c_item["description"])
            if score > best_score:
                best_score, best_j = score, j

        if best_j is not None and best_score >= MATCH_THRESHOLD:
            c_idx, c_item = remaining_customer.pop(best_j)
            qty_match = (
                s_item.get("qty") is not None
                and c_item.get("qty") is not None
                and float(s_item["qty"]) == float(c_item["qty"])
            )
            margin = None
            margin_flag = None
            if s_item.get("unit_price") is not None and c_item.get("unit_price") is not None:
                margin = c_item["unit_price"] - s_item["unit_price"]
                margin_flag = margin <= 0
            matched.append({
                "supplier_item": s_item,
                "customer_item": c_item,
                "similarity": round(best_score, 2),
                "qty_match": qty_match,
                "margin": margin,
                "margin_flag": margin_flag,
            })
        else:
            unmatched_supplier.append(s_item)

    unmatched_customer = [c_item for _, c_item in remaining_customer]
    return {
        "matched": matched,
        "unmatched_supplier": unmatched_supplier,
        "unmatched_customer": unmatched_customer,
    }


def verdict(result):
    issues = []
    for m in result["matched"]:
        desc = m["supplier_item"]["description"][:50]
        if not m["qty_match"]:
            issues.append(f"Qty mismatch on \"{desc}\": supplier {m['supplier_item'].get('qty')} vs customer {m['customer_item'].get('qty')}")
        if m["margin_flag"]:
            issues.append(f"Zero/negative margin on \"{desc}\": buy {m['supplier_item'].get('unit_price')} vs sell {m['customer_item'].get('unit_price')}")
        if m["similarity"] < 0.6:
            issues.append(f"Weak description match (similarity {m['similarity']}) on \"{desc}\"")
    for item in result["unmatched_supplier"]:
        issues.append(f"In supplier PO but not found in customer doc: \"{item['description'][:60]}\"")
    for item in result["unmatched_customer"]:
        issues.append(f"In customer doc but not found in supplier PO: \"{item['description'][:60]}\"")
    return issues
