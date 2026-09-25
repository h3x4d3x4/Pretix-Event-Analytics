"""
Predictive repeat probability scorer.

Lightweight deterministic model (0–100 scale) — no ML dependencies.
Designed to run at two points in time:

  1. At payment time: all factors except check-in are available.
     checkin_completed=False → that +20 factor is simply not awarded yet.

  2. After the event (via analytics_resync): checkin_completed is updated
     from actual check-in data, and the score is recomputed with the full
     signal set.

Scoring weights:
  +40  repeat_count > 0        — strongest signal: came back before
  +20  checkin_completed       — attended in person (post-event only)
  +15  bought_early            — purchased ≥30 days before event start
  +10  ticket_count > 2        — multi-ticket buyers are more invested
  +10  is_local_buyer          — local buyers have lower travel barrier
  +5   is_group_order          — group bookings suggest community engagement
  ───
  100  maximum possible
"""


EARLY_DAYS = 30


def calculate_repeat_probability(order_data: dict) -> int:
    """
    Calculate repeat purchase probability score.

    :param order_data: Dictionary containing the following optional keys:
        - repeat_count (int): Number of previous editions attended
        - checkin_completed (bool): Whether attendee checked in at this event
        - bought_early (bool): True if order placed ≥30 days before event start
        - ticket_count (int): Number of tickets in the order
        - is_local_buyer (bool): True if buyer country matches event home country
        - is_group_order (bool): True if ticket_count > 1

    :returns: Integer score 0–100.
    """
    score = 0

    if order_data.get("repeat_count", 0) > 0:
        score += 40

    if order_data.get("checkin_completed", False):
        score += 20

    if order_data.get("bought_early", False):
        score += 15

    if order_data.get("ticket_count", 1) > 2:
        score += 10

    if order_data.get("is_local_buyer", False):
        score += 10

    if order_data.get("is_group_order", False):
        score += 5

    return min(score, 100)


def score_from_fact(fact) -> int:
    """
    Score a stored fact (model instance or dict with the same keys).
    Every input is persisted on the fact, so the score can be recomputed at
    any time — e.g. after check-in or after series-wide repeat resolution —
    without losing factors such as early purchase.
    """
    get = fact.get if isinstance(fact, dict) else (lambda k, d=None: getattr(fact, k, d))
    days = get("days_before_event")
    return calculate_repeat_probability(
        {
            "repeat_count": get("repeat_count", 0) or 0,
            "ticket_count": get("ticket_count", 1) or 1,
            "is_group_order": bool(get("is_group_order", False)),
            "is_local_buyer": bool(get("is_local_buyer", False)),
            "bought_early": days is not None and days >= EARLY_DAYS,
            "checkin_completed": bool(get("checkin_completed", False)),
        }
    )
