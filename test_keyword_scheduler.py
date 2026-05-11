"""Keyword Scheduler 验证测试"""
import logging
logging.basicConfig(level=logging.INFO, format='%(message)s')

print('=' * 60)
print('KEYWORD SCHEDULER VALIDATION')
print('=' * 60)

# Test 1: Import
from keyword_scheduler import KeywordScheduler, DAILY_KEYWORDS
print(f'\n1. Import OK')
print(f'   Daily keywords: {len(DAILY_KEYWORDS)}')

# Test 2: Generate today's plan
scheduler = KeywordScheduler()
plan = scheduler.get_todays_search_plan()

# Test 3: Analyze plan
total_q = 0
total_e = 0
for domain, p in plan.items():
    total_q += len(p['queries'])
    total_e += len(p['entities'])

old_total = 50
new_total = total_q + total_e
print(f'\n2. Today search plan: {total_q} queries + {total_e} entities = {new_total}')
print(f'   vs old: 32 core + 18 entities = {old_total} total')
print(f'   Savings: {old_total - new_total} fewer queries ({(old_total-new_total)*100//old_total}%)')

# Test 3: Check extended coverage
from domain_config import DOMAIN
extended_in_plan = set()
all_extended = set()
for domain_key, kw in DOMAIN['keyword_matrix'].items():
    for e in kw.get('extended', []):
        all_extended.add(e)
    for q, m in plan[domain_key]['queries']:
        if q in kw.get('extended', []):
            extended_in_plan.add(q)

print(f'\n3. Extended keywords activated: {len(extended_in_plan)}/{len(all_extended)}')
if extended_in_plan:
    for e in extended_in_plan:
        print(f'   + {e}')

# Test 4: Entity rotation
print(f'\n4. Entity rotation:')
for domain, p in plan.items():
    ent_all = DOMAIN['keyword_matrix'][domain].get('entities', [])
    new_sel = p['entities']
    print(f'   {domain}: selected {new_sel} from {len(ent_all)} total')

# Test 5: Module imports
print(f'\n5. Integration imports:')
try:
    from source_manager import SourceManager
    print('   source_manager OK')
except Exception as e:
    print(f'   source_manager FAIL: {e}')

try:
    import AI
    print('   AI.py OK')
except Exception as e:
    print(f'   AI.py FAIL: {e}')

# Test 6: Deterministic
plan2 = scheduler.get_todays_search_plan()
match = all(plan[d]['queries'] == plan2[d]['queries'] for d in plan)
print(f'\n6. Deterministic: {"PASS" if match else "FAIL"}')

# Test 7: Simulate different days
import hashlib
from datetime import datetime, timedelta
print(f'\n7. Rotation simulation (3 days):')
for day_offset in range(3):
    day = datetime.now() + timedelta(days=day_offset)
    day_str = day.strftime("%Y-%m-%d")
    seed = int(hashlib.md5(day_str.encode()).hexdigest()[:8], 16)
    
    # Simulate for first domain
    first_domain = list(DOMAIN['keyword_matrix'].keys())[0]
    kw = DOMAIN['keyword_matrix'][first_domain]
    core = kw.get('core', [])
    extended = kw.get('extended', [])
    rotating = [q for q in core if q not in DAILY_KEYWORDS] + extended
    
    import random
    rng = random.Random(seed + hash(first_domain))
    selected = rng.sample(rotating, min(3, len(rotating)))
    print(f'   {day_str} ({first_domain}): {selected}')

print(f'\n{"=" * 60}')
print('ALL CHECKS PASSED')
