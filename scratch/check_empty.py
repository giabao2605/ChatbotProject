from dotenv import load_dotenv
import os
import urllib.parse
from sqlalchemy import create_engine, text
from qdrant_client import QdrantClient

load_dotenv()

# Check SQL Server
SQL_SERVER = os.getenv("SQL_SERVER", r"localhost\SQLEXPRESS")
SQL_DATABASE = os.getenv("SQL_DATABASE", "Mech_Chatbot_DB")
SQL_DRIVER = os.getenv("SQL_DRIVER", "ODBC Driver 17 for SQL Server")
SQL_USERNAME = os.getenv("SQL_USERNAME")
SQL_PASSWORD = os.getenv("SQL_PASSWORD")

if SQL_USERNAME and SQL_PASSWORD:
    conn_str = (
        f"DRIVER={SQL_DRIVER};"
        f"SERVER={SQL_SERVER};"
        f"DATABASE={SQL_DATABASE};"
        f"UID={SQL_USERNAME};"
        f"PWD={SQL_PASSWORD};"
        f"TrustServerCertificate=yes;"
    )
else:
    conn_str = (
        f"DRIVER={SQL_DRIVER};"
        f"SERVER={SQL_SERVER};"
        f"DATABASE={SQL_DATABASE};"
        f"Trusted_Connection=yes;"
    )

params = urllib.parse.quote_plus(conn_str)
engine = create_engine(f"mssql+pyodbc:///?odbc_connect={params}")

tables = [
    "TaiLieu", 
    "TaiLieuKyThuat", 
    "BangKeVatTu", 
    "DocumentPages", 
    "TechnicalAttributes",
    "IngestionJobs",
    "DocumentFamily",
    "FeedbackReview"
]

print("=== SQL SERVER COUNTS ===")
try:
    with engine.connect() as conn:
        for t in tables:
            try:
                res = conn.execute(text(f"SELECT COUNT(*) FROM {t}"))
                count = res.scalar()
                print(f"Table {t}: {count} rows")
            except Exception as e:
                print(f"Table {t}: Error - {e}")
except Exception as e:
    print("SQL Connection Error:", e)

# Check Qdrant
print("\n=== QDRANT COUNTS ===")
try:
    qdrant_url = os.getenv("QDRANT_URL")
    qdrant_api_key = os.getenv("QDRANT_API_KEY")
    client = QdrantClient(
        url=qdrant_url,
        api_key=qdrant_api_key,
        timeout=120,
    )
    collections = client.get_collections().collections
    for c in collections:
        info = client.get_collection(c.name)
        print(f"Collection {c.name}: {info.points_count} points")
except Exception as e:
    print("Qdrant Error:", e)
