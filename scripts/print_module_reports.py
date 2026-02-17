import sqlite3

conn = sqlite3.connect('data/predator.db')
c = conn.cursor()
rows = list(c.execute('SELECT id,module,status,metrics,created_at FROM module_reports ORDER BY created_at DESC LIMIT 10'))
for r in rows:
    print(r)
conn.close()
