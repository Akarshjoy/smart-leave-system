from datetime import date, timedelta


def days_between(start, end, rule, today=None):
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    today = today or date.today()
    if a < today or b < a:
        raise ValueError('Choose today or a future start date and an end date on or after it.')
    if a.year != b.year:
        raise ValueError('Split requests at December 31; each request must belong to one year.')
    if a.year not in (today.year, today.year + 1):
        raise ValueError('Requests are supported for the current and next calendar year.')
    dates = [(a + timedelta(days=i)).isoformat() for i in range((b-a).days + 1)]
    if len(dates) > int(rule['max_calendar_days']):
        raise ValueError('Request exceeds the configured maximum calendar span.')
    holidays = set(rule.get('holidays', []))
    charged = [d for d in dates if d not in holidays and
               (not rule['working_days_only'] or date.fromisoformat(d).weekday() < 5)]
    if not charged:
        raise ValueError('The selected range contains no chargeable days.')
    return dates, len(charged)


def remaining(rule, balance):
    return int(rule['annual_quota']) + int(balance.get('carry', 0)) - int(balance.get('used', 0))


def validate_rule(data):
    import re
    if not re.fullmatch(r'[a-z][a-z0-9_]{1,30}', data.get('leave_type', '')):
        raise ValueError('Use a lowercase leave type identifier.')
    result = {'leave_type': data['leave_type']}
    for key, low, high in [('annual_quota', 0, 366), ('carry_cap', 0, 366), ('max_calendar_days', 1, 60)]:
        value = data.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ValueError(f'{key} must be an integer between {low} and {high}.')
        result[key] = value
    for key in ['unlimited', 'working_days_only', 'enabled']:
        if not isinstance(data.get(key), bool):
            raise ValueError(f'{key} must be true or false.')
        result[key] = data[key]
    holidays = data.get('holidays', [])
    if not isinstance(holidays, list) or len(holidays) > 60:
        raise ValueError('Provide at most 60 holiday dates.')
    for d in holidays:
        date.fromisoformat(d)
    result['holidays'] = sorted(set(holidays))
    return result
