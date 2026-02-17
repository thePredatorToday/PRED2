from core.database import db

conn = db.connect()
cur = conn.cursor()
print('--- module_reports (last 20) ---')
for row in cur.execute('SELECT module,status,metrics,created_at FROM module_reports ORDER BY created_at DESC LIMIT 20'):
    print(row)

print('\n--- counts ---')
for t in ['coin_updates','learner_suggestions','signals','trades','predictions']:
    try:
        cur.execute(f"SELECT COUNT(*) FROM {t}")
        print(t, cur.fetchone()[0])
    except Exception as e:
        print(t, 'N/A', e)

conn.close()
