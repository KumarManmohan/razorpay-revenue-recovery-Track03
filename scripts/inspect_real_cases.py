import sqlite3
import json
import sys

sys.stdout.reconfigure(encoding='utf-8')

conn = sqlite3.connect('data/recovery.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

cases = cur.execute('SELECT * FROM recovery_cases WHERE id NOT LIKE ?', ('case_demo_%',)).fetchall()
print(f'Total non-demo cases: {len(cases)}')
for c in cases:
    print('='*60)
    for k in c.keys():
        print(f'{k}: {c[k]}')
    
    # Fetch audit events
    events = cur.execute('SELECT * FROM audit_events WHERE case_id = ? ORDER BY id ASC', (c['id'],)).fetchall()
    print(f'--- Audit Events ({len(events)}) ---')
    for e in events:
        print(f"  [{e['id']}] {e['created_at']} | {e['event_type']} | {e['message']}")
        if e['metadata']:
            print(f"      Metadata: {e['metadata']}")

conn.close()
