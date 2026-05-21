from dotenv import load_dotenv
import os
from pathlib import Path

env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

SUPABASE_URL = os.getenv("SUPABASE_URL")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
LANGSMITH_API_KEY = os.getenv("LANGSMITH_API_KEY")
REDIS_URI = os.getenv("REDIS_URI")
DATABASE_URI = os.getenv("DATABASE_URI")

required_env_vars = {
    "SUPABASE_URL": SUPABASE_URL,
    "GOOGLE_API_KEY": GOOGLE_API_KEY,
}

missing = [k for k, v in required_env_vars.items() if not v]

if missing:
    raise ValueError(f"Missing required environment variables: {missing}")
