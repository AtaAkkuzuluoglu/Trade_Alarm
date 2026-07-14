import json
try:
    with open('status.json', 'r') as f:
        d = json.load(f)
        print("DAILY_SYSTEM error:", d.get('status', {}).get('DAILY_SYSTEM', 'NOT FOUND'))
except Exception as e:
    print("Error:", e)
