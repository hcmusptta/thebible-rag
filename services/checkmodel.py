import google.genai as genai
import os

from dotenv import load_dotenv
from google import genai


def list_google_models() -> None:
    """Print the Google models available to the configured API key."""
    load_dotenv()
    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("API_KEY")
    if not api_key:
        raise RuntimeError(
            "Chưa có GOOGLE_API_KEY hoặc API_KEY trong file .env."
        )

    client = genai.Client(api_key=api_key)
    for model in client.models.list():
        print(f"- {model.name}")


if __name__ == "__main__":
    list_google_models()
