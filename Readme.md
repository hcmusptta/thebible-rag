# RAG Chatbot

Ứng dụng Chainlit để hỏi đáp PDF, Word (`.docx`, `.doc`) và TXT tiếng Việt, tập trung vào bản văn Kinh Thánh, giáo lý và chú giải. Hệ thống kết hợp ChromaDB, tìm kiếm từ khóa BM25, embedding và chọn nguồn trước khi tạo câu trả lời.

Đây là ứng dụng tự chạy trên máy cá nhân. GitHub chứa mã nguồn; người dùng tự chuẩn bị model hoặc API key và tài liệu được phép sử dụng. Model, kho dữ liệu và golden testset hiện không được đóng gói sẵn trong repo.

## Yêu cầu

- Khuyến nghị **Python 3.11**, là phiên bản của môi trường kiểm tra hiện tại. Chưa kiểm chứng cài mới trên Python 3.12/3.13; bộ phụ thuộc hiện tại không hỗ trợ Python 3.14.
- [Ollama](https://ollama.com/download) nếu dùng LLM local hoặc chạy benchmark hiện tại.
- Kết nối mạng cho lần cài thư viện/tải model đầu tiên và khi gọi Google API.
- Node.js và npm chỉ khi cần đọc `.doc`; PDF, DOCX và TXT không cần bộ đọc Node.js.
- Đủ RAM/VRAM cho các model đã chọn. Chưa có cấu hình phần cứng tối thiểu được nghiệm thu. Chuyển LLM sang API vẫn giữ embedding và reranker local.

## 1. Cài thư viện

Mở PowerShell tại thư mục có `main.py` và `requirements.txt`:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip check
```

`requirements.txt` khóa các phiên bản chính dựa trên môi trường hiện tại và ràng buộc `fsspec` tương thích với `datasets`. Đây chưa phải lockfile của toàn bộ phụ thuộc bắc cầu. Không sao chép môi trường ảo từ máy khác.

Nếu cần PyTorch cho một bản CUDA cụ thể, chọn lệnh tại [PyTorch](https://pytorch.org/get-started/locally/) và giữ phiên bản tương thích với `requirements.txt`.

Chỉ khi cần đọc DOC cũ:

```powershell
npm ci --prefix loaddocs/doc_reader --ignore-scripts --omit=dev
```

## 2. Tạo cấu hình riêng

Chỉ tạo `.env` nếu chưa có:

```powershell
if (-not (Test-Path -LiteralPath .env)) {
    Copy-Item -LiteralPath .env.example -Destination .env
}
```

`.env.example` là mẫu, không được nạp trực tiếp. Không chép đè mẫu lên `.env` của kho đang dùng. Ứng dụng đọc `.env` và biến môi trường tiến trình; biến đã có trong môi trường được ưu tiên. Khởi động lại ứng dụng sau khi đổi cấu hình.

### Ollama local — mặc định của cấu hình mẫu

```dotenv
LLM_PROVIDERS=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen3:8b
OLLAMA_TEMPERATURE=0
```

Tải đúng model được cấu hình:

```powershell
ollama pull qwen3:8b
```

Ollama không cần Google API key. Nếu chọn `qwen3.5:9b` hoặc model khác, đổi `OLLAMA_MODEL` rồi tải đúng tên đó bằng `ollama pull`. Model thay thế phải đáp ứng yêu cầu đầu ra JSON theo schema; chưa bảo đảm chất lượng tương đương giữa các model.

Ollama phải đang phục vụ tại `OLLAMA_BASE_URL`. Nếu ứng dụng Ollama đã chạy nền, có thể dùng server đó. Muốn khởi động server riêng với tối đa một model được giữ trong bộ nhớ, dừng server cũ trước rồi chạy ở terminal riêng:

```powershell
.\start_ollama.ps1
```

Script đặt `OLLAMA_MAX_LOADED_MODELS=1` cho `ollama serve`. Thêm biến này vào `.env` của Chainlit không thay đổi một Ollama server đã chạy.

### Google API — tùy chọn cho LLM trả lời

```dotenv
LLM_PROVIDERS=google
GOOGLE_API_KEY=your_google_api_key
GOOGLE_MODEL=your_available_gemini_model
```

Thay hai giá trị mẫu bằng key của bạn và model thực sự khả dụng trong Google AI Studio, có hỗ trợ JSON theo schema. `.env.example` dùng alias `gemini-flash-latest`; kiểm tra quyền truy cập của project trước khi dùng.

Một câu hỏi có thể gọi LLM nhiều lần để lập truy vấn, chọn nguồn, trả lời và kiểm chứng. API miễn phí có giới hạn request/token; xem [hạn mức Gemini API](https://ai.google.dev/gemini-api/docs/rate-limits). Benchmark hiện vẫn dùng Ollama dù chatbot chọn Google.

### Nhánh Hugging Face

`LLM_PROVIDERS=hf` hiện dùng `transformers.pipeline()` để tải và chạy `HF_MODEL` **trên máy local**. Đây không phải Hugging Face Inference API. `HF_API_KEY` và `HF_TIMEOUT` hiện không được truyền vào pipeline như cấu hình API từ xa; lựa chọn này không loại bỏ yêu cầu phần cứng local.

### Embedding, reranker và kho dữ liệu

```dotenv
CHROMA_DB_PATH=bible_workflow_data
CHROMA_COLLECTION=bible_workflow_v4
EMBEDDING_MODEL_NAME=AITeamVN/Vietnamese_Embedding_v2
EMBEDDING_DIMENSION=1024
EMBEDDING_DEVICE=auto
RERANKER_MODEL_NAME=AITeamVN/Vietnamese_Reranker
RERANKER_DEVICE=auto
RERANK_ENABLED=true
```

- Tên model Hugging Face được tải khi cần. Có thể dùng đường dẫn tới model local đã tải đầy đủ.
- `EMBEDDING_DEVICE` và `RERANKER_DEVICE` nhận `auto`, `cpu`, `cuda`. `auto` chọn CUDA khi PyTorch nhận GPU. Dùng `cpu` để chuyển phần tương ứng khỏi GPU; tốc độ phụ thuộc máy.
- `RERANK_ENABLED=false` tắt CrossEncoder ở nhánh sử dụng nó. LLM chọn bằng chứng trong luồng Kinh Thánh vẫn có thể được gọi.
- `CHROMA_DB_PATH` tương đối tính từ thư mục ứng dụng. Kho Kinh Thánh cần Chroma và `scripture.sqlite3` cùng phiên nạp; sao lưu toàn bộ thư mục dữ liệu.
- Collection kiểm tra định danh embedding, số chiều và schema. Đường dẫn local và tên Hugging Face có thể bị coi là hai định danh khác nhau. Giữ cấu hình đã tạo kho; không bỏ kiểm tra tương thích để mở kho.
- Khi thay model embedding hoặc số chiều, dùng **collection mới và nạp lại tài liệu**. Chỉ đổi LLM trả lời không yêu cầu tạo lại vector.

## 3. Chạy ứng dụng

Tại thư mục ứng dụng, trong môi trường ảo đã kích hoạt:

```powershell
python -m chainlit run main.py --host 127.0.0.1 --port 8000
```

Mở `http://localhost:8000`. Thêm `-w` để tự tải lại khi phát triển. Dùng `--port` để đổi cổng; không dựa vào biến `PORT` tùy ý trong `.env`.

`run_chainlit.py` xử lý riêng vấn đề event loop/biến `DEBUG` của launcher; script này không làm Python 3.14 trở thành phiên bản được bộ phụ thuộc hỗ trợ.

## 4. Nạp tài liệu và hỏi đáp

1. Trong Cài đặt, chọn loại tài liệu sẽ nạp, nguồn dùng để trả lời, bản tài liệu/bản dịch và tên bản dịch nếu biết. Mặc định tự nhận diện loại tải lên và tìm trên tất cả các nguồn.
2. Bấm **Thêm tài liệu** hoặc kéo PDF/DOCX/DOC/TXT vào chat. Lần đầu có thể chờ tải model. Không cần LLM để tạo embedding tài liệu.
3. Kiểm tra thông báo nạp, cảnh báo và nguồn đính kèm khi hỏi đáp. Chọn đúng loại nếu nhận diện tự động không phù hợp.
4. **Kho tài liệu** cho phép xem, tóm tắt và xóa tài liệu. **Xóa toàn bộ** reset collection vector sau xác nhận; bản gốc, export và snapshot audit trên đĩa không tự bị xóa.

PDF mặc định tối đa 100 MB và 3.500 trang, theo `.env`. DOCX/TXT dùng vị trí đoạn/dòng thay cho số trang PDF. TXT cần Unicode UTF-8 hoặc UTF-16 có BOM. DOC phụ thuộc bộ đọc Node.js ở bước 1.

Bộ đọc PDF Kinh Thánh dựa vào bookmark/bố cục và nhãn chương–câu; Word/TXT cần tên sách, chương và nhãn câu đủ rõ. Đọc được text không đồng nghĩa nhận diện được cấu trúc Kinh Thánh. Nội dung OCR hoặc cấu trúc chưa tin cậy có thể bị cách ly và không được dùng trả lời.

Có thể xuất và kiểm tra PDF trước khi nạp, không đổi collection:

```powershell
python -X utf8 scripts/normalize_scripture.py "path/to/Bible.pdf" --output bible_audit
```

Thêm `--profile bible_profile.example.json` sau khi điền hồ sơ đúng bản nguồn, `--chapter GEN:1` để chọn phạm vi, hoặc `--text` để xuất TXT có cấu trúc. Đọc `report.json` và `quarantined.jsonl`; kiểm tra tự động không xác nhận bản dịch đúng hay kho đã trọn bộ. Chỉ dùng `--collection` khi chủ động nạp vào collection mới.

## 5. Kiểm thử và benchmark

### Kiểm thử chức năng

```powershell
python -X utf8 -m unittest discover -s tests -v
```

Kiểm thử tích hợp tạo dữ liệu tạm và giả lập model. Module vector store vẫn mở collection mặc định khi import; dùng cấu hình `CHROMA_DB_PATH`/`CHROMA_COLLECTION` riêng nếu không muốn mở kho đang dùng. Test đạt không thay thế đánh giá model thật.

### Benchmark RAGAS

Chuẩn bị golden testset JSON và đặt đường dẫn trong `.env`, ví dụ:

```dotenv
GOLDEN_TESTSET_PATH=./golden_testset.json
RAGAS_SAMPLE_SIZE=25
RAGAS_JUDGE_MODEL=qwen3.5:9b
RAGAS_EMBEDDING_MODEL=AITeamVN/Vietnamese_Embedding_v2
```

Đường dẫn testset tương đối tính từ thư mục chạy lệnh. File cần là mảng JSON; mỗi mẫu có `id`, `user_input`, `reference_contexts` (danh sách đoạn nguồn), `reference` (đáp án chuẩn), và có thể thêm `metadata`. Đáp án/đoạn nguồn phải khớp kho và bản dịch được đánh giá. Nếu không đặt biến, code tìm `golden_testset.json` ở thư mục ứng dụng rồi thư mục cha; file này hiện chưa nằm trong repo.

Nếu dùng judge mặc định, tải thêm model:

```powershell
ollama pull qwen3.5:9b
```

Bấm **Đánh giá RAG** trong ứng dụng:

- **Nhanh**: mặc định chấm `non_llm_context_recall`; bước truy xuất vẫn có thể gọi Ollama để lập truy vấn/chọn nguồn.
- **Đầy đủ**: thêm các chỉ số nội dung với judge và embedding đánh giá.
- Cả hai dùng `OLLAMA_MODEL` cho các bước retrieval/trả lời cần LLM; judge chọn riêng bằng `RAGAS_JUDGE_MODEL`. Điểm này không chứng nhận chatbot Google nếu chưa thay luồng benchmark.
- Embedding đánh giá cùng model thì dùng chung instance. Model khác dùng `RAGAS_EMBEDDING_DEVICE=cpu` mặc định.

Bộ câu hỏi cơ bản riêng có thể chạy với model thật sau khi kho sẵn sàng:

```powershell
python -X utf8 evaluate_bible_answers.py --answers --output bible_workflow_data/evaluation/basic-current.json
```

So khớp tham chiếu/từ khóa chỉ hỗ trợ kiểm tra hồi quy. Cần đọc lại đáp án và bằng chứng; điểm RAGAS hay trạng thái có dẫn nguồn không chứng minh mọi diễn giải đều đúng.

## Phục hồi và lỗi thường gặp

| Hiện tượng | Cách kiểm tra |
|---|---|
| Collection khác embedding model/dimension/schema | Khôi phục cấu hình đã tạo kho. Nếu thật sự đổi embedding, chọn collection mới và nạp lại; không xóa kho cũ để thử. |
| Ollama không kết nối hoặc thiếu model | Kiểm tra server, `OLLAMA_BASE_URL`, `ollama list`; tải đúng model trong cấu hình. |
| Google hết quota hoặc key/model không hợp lệ | Kiểm tra project, quyền model và hạn mức; không thử lại vô hạn. |
| Thiếu bộ đọc DOC | Cài Node.js, chạy `npm ci` ở bước 1 hoặc lưu thành DOCX. |
| `name 'true' is not defined` | Boolean Python là `True`/`False`; chuỗi cấu hình `.env` và JSON có quy tắc riêng. |
| Thiếu Markdown `en-US` | Chainlit dùng `chainlit.md` mặc định; đây không phải lỗi benchmark. |
| Kho đang bận | Chờ nạp xong. Hủy nạp cần chờ batch hiện tại và rollback được xác nhận. |

Phiên nạp có nhật ký: khi khởi động lại, dữ liệu đang ghi dở được rollback; phiên đã quyết định commit được hoàn tất. Dữ liệu cũ được giữ đến khi phiên mới đủ điều kiện công bố. Kho cũ thiếu nhật ký hoặc danh tính không được tự sửa chỉ để vượt kiểm tra.

## Cấu trúc và chia sẻ mã nguồn

| Đường dẫn | Vai trò |
|---|---|
| `main.py` | Giao diện, cài đặt phiên chat, tải tài liệu và action. |
| `loaddocs/` | Bộ đọc, chuẩn hóa cấu trúc và embedding. |
| `services/readdocs.py` | Nạp, thay phiên bản, hủy và phục hồi dữ liệu. |
| `services/ans.py` | Định tuyến câu hỏi, tìm nguồn và dựng đáp án. |
| `services/model.py` | Provider LLM, sinh/kiểm tra claim và tóm tắt. |
| `vectordb/vector_store.py` | Chroma, BM25 và khóa/giao dịch collection. |
| `services/eval_ragas.py` | Chuẩn bị và chạy benchmark. |
| `tests/` | Kiểm thử chức năng và hồi quy. |

`chainlit.md` là nội dung giới thiệu được Chainlit đọc; các Markdown hướng dẫn khác không tự trở thành system prompt hoặc dữ liệu RAG.
