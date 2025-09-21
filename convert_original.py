import json
import os


def load_original(path):
    with open(path, 'r') as f:
        data = f.read()
    # original.txt may contain triple-backticked content; strip surrounding ``` if present
    data = data.strip()
    if data.startswith('```') and data.endswith('```'):
        # remove first and last lines if they are ``` markers
        parts = data.splitlines()
        if parts[0].strip().startswith('```'):
            parts = parts[1:]
        if parts and parts[-1].strip().endswith('```'):
            parts = parts[:-1]
        data = '\n'.join(parts)
    return json.loads(data)


def map_card(card):
    # Basic mapping to the target schema used in credit_cards.txt
    mapped = {
        'issuer': card.get('issuer', ''),
        'name': card.get('name', ''),
        'annual_fee': card.get('annualFee', 0),
        'rewards': {},
        'point_value': 0.01,
        'rebates': []
    }

    # Attempt to infer rewards categories from universalCashbackPercent and known strings
    u = card.get('universalCashbackPercent')
    if isinstance(u, (int, float)):
        mapped['rewards']['other'] = u

    # If credits exist, turn them into flat rebates
    for c in card.get('credits', []):
        desc = c.get('description') or c.get('description', '')
        amt = c.get('value', 0)
        mapped['rebates'].append({'type': 'flat', 'description': desc, 'amount': amt})

    # For certain common names, add simple category rebates
    name = mapped['name'].lower()
    if 'sapphire reserve' in name or 'sapphire' in name:
        mapped['rewards'].update({'flights_portal': 3, 'dining': 3})
    if 'gold' in name and 'amex' in card.get('issuer','').lower() or 'gold' in name:
        mapped['rewards'].update({'restaurants': 4, 'supermarkets': 4, 'flights_portal': 3})
    if 'venture x' in name.lower() or 'venture' in name.lower():
        mapped['rewards'].update({'flights_portal': 5, 'hotels_portal': 10})
    if 'capital_one' in card.get('issuer','').lower() or card.get('issuer','').lower()=='capital_one':
        # ensure a reasonable default
        mapped['rewards'].setdefault('other', 1)

    # turn some offers into a simple rebate if they look like statement credits (take first offer amount)
    offers = card.get('offers') or []
    if offers:
        first = offers[0]
        amounts = first.get('amount', [])
        if amounts and isinstance(amounts, list):
            a = amounts[0].get('amount') if isinstance(amounts[0], dict) else amounts[0]
            # if amount small (<=1000) treat as flat rebate
            try:
                if a and float(a) <= 1000:
                    mapped['rebates'].append({'type': 'flat', 'description': 'Signup/Offer', 'amount': float(a)})
            except Exception:
                pass

    return mapped


def main():
    base = os.path.dirname(os.path.abspath(__file__))
    orig_path = os.path.join(base, 'original.txt')
    out_path = os.path.join(base, 'credit_cards_converted.txt')

    cards = load_original(orig_path)
    mapped = [map_card(c) for c in cards]

    with open(out_path, 'w') as f:
        json.dump(mapped, f, indent=4)

    print('Wrote', out_path)


if __name__ == '__main__':
    main()
