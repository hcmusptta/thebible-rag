import os

from dotenv import load_dotenv
from services import eval_ragas

load_dotenv()

testset_path = eval_ragas._resolve_golden_testset_path()

print(f"[RAGAS] GOLDEN_TESTSET_PATH env: {os.getenv('GOLDEN_TESTSET_PATH')!r}")
print(f"[RAGAS] File đang dùng: {os.path.abspath(testset_path)}")
print(f"[RAGAS] File tồn tại: {os.path.exists(testset_path)}")