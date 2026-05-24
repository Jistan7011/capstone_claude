from pathlib import Path
import os

# 프로젝트 루트 기준 경로
BASE_DIR = Path(__file__).resolve().parent

# 문서 들어갈 폴더 (PDF, txt 등)
DATA_DIR = BASE_DIR / "data"

# Chroma 벡터DB 폴더
DB_DIR = BASE_DIR / "db"

# OpenAI 관련 설정
# ▶ 환경변수에 OPENAI_API_KEY 반드시 넣어야 함
#    예) Windows PowerShell:  setx OPENAI_API_KEY "sk-...."
OPENAI_API_KEY ='' # open_ai_key.pem

# 사용할 임베딩/챗모델 이름 (필요하면 수정)
EMBEDDING_MODEL = "text-embedding-3-small"
CHAT_MODEL = "gpt-4.1-mini"


