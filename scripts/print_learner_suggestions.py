"""Print recent learner suggestions from DB (smoke helper)."""
from core.database import db

if __name__ == "__main__":
    rows = db.fetch_recent_learner_suggestions(limit=100)
    for r in rows:
        print(r)
