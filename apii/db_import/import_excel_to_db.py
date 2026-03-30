import os
import pandas as pd
from sqlalchemy import create_engine
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")
load_dotenv(dotenv_path=ENV_PATH, override=True)

DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
EXCEL_PATH = os.getenv("EXCEL_PATH")

print("ENV PATH:", ENV_PATH)
print("ENV CHECK:", DB_HOST, DB_PORT, DB_NAME, DB_USER, EXCEL_PATH)

required = {
    "DB_HOST": DB_HOST,
    "DB_PORT": DB_PORT,
    "DB_NAME": DB_NAME,
    "DB_USER": DB_USER,
    "DB_PASSWORD": DB_PASSWORD,
    "EXCEL_PATH": EXCEL_PATH,
}
missing = [k for k, v in required.items() if not v]
if missing:
    raise RuntimeError(f"Missing env vars: {missing} (check prophet/db_import/.env)")

excel_full_path = EXCEL_PATH
if not os.path.isabs(excel_full_path):
    excel_full_path = os.path.join(BASE_DIR, EXCEL_PATH)

engine = create_engine(
    f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
)

df = pd.read_excel(excel_full_path)
df.to_sql("sales_raw", engine, if_exists="replace", index=False)

print(" Data imported successfully ")