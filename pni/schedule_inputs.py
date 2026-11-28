from datetime import datetime


def parse_dispatch_slot(beijing_date, edition, scheduled_beijing):
    slot_times = {"morning": "06:30:00", "evening": "19:30:00"}
    if edition not in slot_times:
        raise ValueError("Invalid dispatch edition")
    expected = f"{beijing_date}T{slot_times[edition]}+08:00"
    if scheduled_beijing != expected:
        raise ValueError("Dispatch time does not match its Beijing date and edition")
    nominal = datetime.fromisoformat(expected)
    if nominal.isoformat() != expected:
        raise ValueError("Dispatch slot requires an ISO Beijing date and time")
    return nominal
