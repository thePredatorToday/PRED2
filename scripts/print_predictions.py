import sqlite3

conn = sqlite3.connect('data/predator.db')
c = conn.cursor()
for row in c.execute('SELECT id,mint,symbol,predicted_mcap,predicted_price,followup_sent,actual_mcap,actual_price,result,created_at FROM predictions'):
    print(row)
conn.close()
