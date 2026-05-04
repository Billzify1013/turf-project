from decimal import Decimal
from datetime import datetime, timedelta


def calculate_price(turf, booking_date, start_time, end_time, crosses_midnight=False, debug=False):
    """
    Auto-calculate price for a booking based on pricing rules.
    Supports overnight rules (e.g. 5 PM → 6 AM).
    Set debug=True to print which rule applies to each block.
    """
    from bookings.models import PricingRule
    from datetime import timedelta as _td

    default_rate = turf.default_price_per_hour
    rules = list(PricingRule.objects.filter(turf=turf, is_active=True))

    start_dt = datetime.combine(booking_date, start_time)
    end_dt = datetime.combine(booking_date, end_time)
    if crosses_midnight or end_dt <= start_dt:
        end_dt += _td(days=1)

    total_min = int((end_dt - start_dt).total_seconds() / 60)
    if total_min <= 0:
        return Decimal('0')

    BLOCK = 15
    total_price = Decimal('0')
    cur = start_dt

    def rule_covers(rule, block_time):
        """Does this rule cover the given time? Handles overnight rules."""
        rs, re = rule.start_time, rule.end_time
        # Normal rule: start < end (e.g. 9:00 to 17:00)
        if rs < re:
            return rs <= block_time < re
        # Overnight rule: start > end (e.g. 17:00 to 06:00)
        # Covers: time >= start OR time < end
        if rs > re:
            return block_time >= rs or block_time < re
        # rs == re means full 24 hours? treat as covering all
        return True

    if debug:
        print(f"[PRICING DEBUG] booking_date={booking_date}, start={start_time}, end={end_time}, crosses_midnight={crosses_midnight}")
        print(f"[PRICING DEBUG] start_dt={start_dt}, end_dt={end_dt}")
        print(f"[PRICING DEBUG] rules count: {len(rules)}")
        for r in rules:
            print(f"  - Rule #{r.id} '{r.name}': {r.start_time}–{r.end_time}, day={r.day_of_week}, date={r.specific_date}, price={r.price_per_hour}, mult={r.multiplier}")

    while cur < end_dt:
        blk_end = min(cur + timedelta(minutes=BLOCK), end_dt)
        blk_min = int((blk_end - cur).total_seconds() / 60)
        bs = cur.time()
        match_date = cur.date()

        rule = None
        # 1. Specific date
        for r in rules:
            if r.specific_date == match_date and rule_covers(r, bs):
                rule = r
                break
        # 2. Day of week
        if not rule:
            dow = match_date.weekday()
            for r in rules:
                if r.specific_date is None and r.day_of_week == dow and rule_covers(r, bs):
                    rule = r
                    break
        # 3. Any-day time-only rule
        if not rule:
            for r in rules:
                if r.specific_date is None and r.day_of_week is None and rule_covers(r, bs):
                    rule = r
                    break

        rate = rule.get_effective_price(default_rate) if rule else default_rate

        if debug:
            rname = f"Rule #{rule.id}" if rule else "DEFAULT"
            print(f"  Block {bs} on {match_date} → rate ₹{rate}/hr ({rname})")

        total_price += (rate / 60) * blk_min
        cur = blk_end

    if debug:
        print(f"[PRICING DEBUG] TOTAL: ₹{total_price}")

    return total_price.quantize(Decimal('1'))


def check_overlap(turf, booking_date, start_time, end_time, exclude_id=None):
    from bookings.models import Booking
    from datetime import timedelta as _td

    # Overnight booking: split into 2 days
    if end_time <= start_time:
        from datetime import time as _time
        next_date = booking_date + _td(days=1)
        # Today: from start_time to end of day
        qs1 = Booking.objects.filter(
            turf=turf, booking_date=booking_date,
            status__in=['booked', 'hold'],
            end_time__gt=start_time,
        )
        # Tomorrow: from midnight to end_time
        qs2 = Booking.objects.filter(
            turf=turf, booking_date=next_date,
            status__in=['booked', 'hold'],
            start_time__lt=end_time,
        )
        if exclude_id:
            qs1 = qs1.exclude(id=exclude_id)
            qs2 = qs2.exclude(id=exclude_id)
        return qs1.exists() or qs2.exists()

    # Normal same-day booking
    qs = Booking.objects.filter(
        turf=turf, booking_date=booking_date,
        status__in=['booked', 'hold'],
        start_time__lt=end_time,
        end_time__gt=start_time,
    )
    if exclude_id:
        qs = qs.exclude(id=exclude_id)
    return qs.exists()