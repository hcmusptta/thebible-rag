import re
import unicodedata
from functools import lru_cache

from langchain_text_splitters import RecursiveCharacterTextSplitter


class DocumentProcessor:
    def __init__(self, chunk_size: int = 1000, chunk_overlap: int = 200) -> None:
        self._validate_chunk_params(chunk_size, chunk_overlap)
        self.default_chunk_size = chunk_size
        self.default_chunk_overlap = chunk_overlap
        self.separators = ("\n\n", "\n", " ", "")

    @staticmethod
    def _validate_chunk_params(chunk_size: int, chunk_overlap: int) -> None:
        if chunk_size <= 0:
            raise ValueError("chunk_size phải lớn hơn 0.")
        if chunk_overlap < 0:
            raise ValueError("chunk_overlap không được âm.")
        if chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap phải nhỏ hơn chunk_size.")

    def _build_splitter(
        self,
        chunk_size: int,
        chunk_overlap: int,
    ) -> RecursiveCharacterTextSplitter:
        self._validate_chunk_params(chunk_size, chunk_overlap)
        return RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=list(self.separators),
            length_function=len,
        )

    def _choose_chunk_params(self, text_length: int) -> tuple[int, int]:
        if text_length <= 4000:
            return 700, 120
        if text_length <= 12000:
            return 900, 150
        return self.default_chunk_size, self.default_chunk_overlap

    def clean_text(self, text: str) -> str:
        if not text:
            return ""
        text = unicodedata.normalize("NFC", text)
        text = re.sub(r"(?<!\s)-\n", "", text)
        text = text.replace("\n", " ")
        return re.sub(r"\s+", " ", text).strip()

    def chunk_text(
        self,
        text: str,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
    ) -> list[str]:
        if not text:
            return []

        adaptive_size, adaptive_overlap = self._choose_chunk_params(len(text))
        chunk_size = chunk_size if chunk_size is not None else adaptive_size
        chunk_overlap = (
            chunk_overlap if chunk_overlap is not None else adaptive_overlap
        )
        splitter = self._build_splitter(chunk_size, chunk_overlap)
        return splitter.split_text(text)

    def chunk_parent_child_text(
        self,
        text: str,
        parent_chunk_size: int = 900,
        parent_chunk_overlap: int = 120,
        child_chunk_size: int = 200,
        child_chunk_overlap: int = 40,
    ) -> list[dict[str, object]]:
        if not text:
            return []

        parent_splitter = self._build_splitter(
            parent_chunk_size,
            parent_chunk_overlap,
        )
        child_splitter = self._build_splitter(
            child_chunk_size,
            child_chunk_overlap,
        )

        hierarchy: list[dict[str, object]] = []
        parent_chunks = parent_splitter.split_text(text)
        for parent_index, parent_chunk in enumerate(parent_chunks):
            child_chunks = child_splitter.split_text(parent_chunk) or [parent_chunk]
            for child_index, child_chunk in enumerate(child_chunks):
                hierarchy.append(
                    {
                        "parent_index": parent_index,
                        "parent_total": len(parent_chunks),
                        "parent_content": parent_chunk,
                        "child_index": child_index,
                        "child_total": len(child_chunks),
                        "child_content": child_chunk,
                    }
                )

        return hierarchy

@lru_cache(maxsize=1)
def get_processor() -> DocumentProcessor:
    return DocumentProcessor()
