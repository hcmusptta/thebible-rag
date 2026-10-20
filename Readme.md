# RAG Chatbot

Ứng dụng Chainlit để hỏi đáp PDF, Word (`.docx`, `.doc`) và TXT tiếng Việt, tập trung vào bản văn Kinh Thánh, giáo lý và chú giải. Hệ thống kết hợp ChromaDB, tìm kiếm từ khóa BM25, embedding và chọn nguồn trước khi tạo câu trả lời.

Đây là ứng dụng tự chạy trên máy cá nhân. GitHub chứa mã nguồn và tài liệu mẫu tự soạn trong `samples/`, kèm `golden_testset.json` để thử cài đặt. Người dùng tự chuẩn bị model hoặc API key và tài liệu được phép sử dụng. Repo không đóng gói model hoặc kho Kinh Thánh đã được chuyên gia nghiệm thu.

## Yêu cầu

- Khuyến nghị **Python 3.11**, là phiên bản của môi trường kiểm tra hiện tại. Chưa kiểm chứng cài mới trên Python 3.12/3.13; bộ phụ thuộc hiện tại không hỗ trợ Python 3.14.
- [Ollama](https://ollama.com/download) nếu dùng LLM local hoặc các metric RAGAS cần LLM judge.
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

`requirements.txt` khóa các phiên bản chính, ràng buộc `fsspec` tương thích với `datasets` và dùng `langchain-community==0.4.1` cho [lỗi import của RAGAS 0.4.3](https://github.com/vibrantlabsai/ragas/issues/2745). Các adapter OpenAI/Instructor được khóa cùng nhau để tránh resolver chọn bộ quá cũ. Đây chưa phải lockfile của toàn bộ phụ thuộc bắc cầu. Không sao chép môi trường ảo hoặc sửa `site-packages` để làm ứng dụng chạy.

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
OLLAMA_REASONING=false
LLM_TIMEOUT=120
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

Một câu hỏi có thể gọi LLM nhiều lần để lập truy vấn, chọn nguồn, trả lời và kiểm chứng. API miễn phí có giới hạn request/token; xem [hạn mức Gemini API](https://ai.google.dev/gemini-api/docs/rate-limits). Benchmark dùng cùng provider trả lời với chatbot; các metric cần judge vẫn dùng Ollama riêng.

Để giảm tải máy, có thể thử Google cho LLM, `EMBEDDING_DEVICE=cpu`, `RERANK_ENABLED=false`. Đây là cấu hình thử nghiệm chưa được nghiệm thu chất lượng hoặc hiệu năng trên máy yếu. Embedding vẫn chạy local; khi thay embedding cần kho mới. Không có cam kết rằng quota miễn phí đủ cho benchmark hoặc nhiều người dùng. Giảm số mẫu và đo request/token trước khi chạy hàng loạt.

### Nhánh Hugging Face

`LLM_PROVIDERS=hf` hiện dùng `transformers.pipeline()` để tải và chạy `HF_MODEL` **trên máy local**. Đây không phải Hugging Face Inference API. `HF_API_KEY` và `HF_TIMEOUT` hiện không được truyền vào pipeline như cấu hình API từ xa; lựa chọn này không loại bỏ yêu cầu phần cứng local.

### Embedding, reranker và kho dữ liệu

```dotenv
CHROMA_DB_PATH=bible_workflow_data
CHROMA_COLLECTION=bible_workflow_v4
EMBEDDING_MODEL_NAME=AITeamVN/Vietnamese_Embedding_v2
EMBEDDING_DIMENSION=1024
EMBEDDING_DEVICE=cpu
RERANKER_MODEL_NAME=AITeamVN/Vietnamese_Reranker
RERANKER_DEVICE=cpu
RERANK_ENABLED=true
```

- Tên model Hugging Face được tải khi cần. Có thể dùng đường dẫn tới model local đã tải đầy đủ.
- `EMBEDDING_DEVICE` và `RERANKER_DEVICE` nhận `auto`, `cpu`, `cuda`. `auto` chọn CUDA khi PyTorch nhận GPU. Dùng `cpu` để chuyển phần tương ứng khỏi GPU; tốc độ phụ thuộc máy.
- Mẫu cấu hình dùng CPU cho encoder để dành VRAM cho Ollama. Trên máy RTX 5060 Laptop 8 GB đã thử, lượt encoder `auto` gặp chậm/timeout; lượt embedding CPU hoàn thành cả ba câu retrieval thử. Đây là pilot trên một máy với trạng thái tải model khác nhau, không phải cấu hình tối thiểu. Chỉ đổi sang `auto`/`cuda` sau khi đo bộ nhớ và độ trễ của cả hệ thống.
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

Để thử từ repo mới, nạp `samples/onboarding_sample.txt` với loại **generic**, rồi hỏi “Dự án Atlas lưu tài liệu ở đâu?”. Dùng bộ `golden_testset.json` đi kèm cho kho có tài liệu mẫu này; nó không đánh giá các nguồn Kinh Thánh của bạn.

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

Đường dẫn testset tương đối tính từ thư mục chạy lệnh. File cần là mảng JSON; mỗi mẫu có `id`, `user_input`, `reference_contexts` (danh sách đoạn nguồn), `reference` (đáp án chuẩn), và có thể thêm `metadata`. Đáp án/đoạn nguồn phải khớp kho và bản dịch được đánh giá. File đi kèm repo chỉ gồm dữ liệu tự soạn cho onboarding. Testset Kinh Thánh cũ cần được đối chiếu lại nguồn, bản dịch và phiên bản kho trước khi dùng làm bằng chứng nghiệm thu.

Nếu dùng judge mặc định, tải thêm model:

```powershell
ollama pull qwen3.5:9b
```

Bấm **Đánh giá RAG** trong ứng dụng:

- **Nhanh**: mặc định chấm `non_llm_context_recall`; bước truy xuất vẫn có thể gọi provider đang chọn để lập truy vấn/chọn nguồn.
- **Đầy đủ**: thêm các chỉ số nội dung với judge và embedding đánh giá.
- Cả hai dùng `LLM_PROVIDERS` cho các bước retrieval/trả lời cần LLM; judge chọn riêng bằng `RAGAS_JUDGE_MODEL`. Kết quả được lưu trong thư mục `evaluation/` của kho, kèm cấu hình, mã băm code/testset, danh mục nguồn và revision collection. `requests` đếm lần gọi provider sau kiểm tra đầu vào cục bộ; SDK retry có thể tạo thêm lần gọi HTTP. Token chỉ cộng khi provider báo usage: `token_reports=0` nghĩa là chưa biết, không phải miễn phí. Diagnostics tính bước chuẩn bị câu trả lời, không cộng các lần chấm của RAGAS judge.
- Embedding đánh giá cùng model thì dùng chung instance. Model khác dùng `RAGAS_EMBEDDING_DEVICE=cpu` mặc định.

Bộ câu hỏi cơ bản riêng có thể chạy với model thật sau khi kho sẵn sàng:

```powershell
python -X utf8 evaluate_bible_answers.py --answers --output bible_workflow_data/evaluation/basic-current.json
```

So khớp tham chiếu/từ khóa chỉ hỗ trợ kiểm tra hồi quy. Cần đọc lại đáp án và bằng chứng; điểm RAGAS hay trạng thái có dẫn nguồn không chứng minh mọi diễn giải đều đúng.

`regression_passed` ghi kết quả kiểm tra tự động. Đáp án do model sinh giữ `passed=null` cho đến khi người đọc chấm; exit code 2 nghĩa là còn chờ duyệt. Quy trình chấm, hiệu chuẩn judge, tạo holdout và so sánh từng bước nằm trong [evaluation/REVIEW_GUIDE.md](evaluation/REVIEW_GUIDE.md).

```powershell
python scripts/benchmark_ablation.py --cases bible_basic_questions.json --output bible_workflow_data/evaluation/ablation.json
python scripts/audit_corpus.py --output bible_workflow_data/evaluation/corpus-review.json
python scripts/probe_models.py --provider ollama --output release_checks/ollama.json
python scripts/check_release.py --output release_checks/files.json
```

Ablation mặc định so retrieval trên cùng câu hỏi/kho; `--answers` thêm bản nháp offline và đáp án kiểm chứng để người đọc so sánh. Không diễn giải hit-rate truy xuất thành chất lượng câu trả lời. Probe provider chỉ gọi một yêu cầu JSON tổng hợp, không phải benchmark toàn bộ RAG.

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

## Giới hạn hiện tại

- Chưa nghiệm thu cài mới trên nhiều hệ điều hành hoặc hiệu năng máy yếu. Tên module `loaddocs/document.py` và `loaddocs/embedding.py` trong Git đã dùng chữ thường.
- `OLLAMA_REASONING=false` là mặc định; có thể bật `true` nếu model hỗ trợ. Provider từ chối đầu ra kết thúc do hết token và kiểm tra JSON/schema trước khi dùng; cần đo lại chất lượng khi thay model/reasoning.
- Mọi nguồn generic/nguồn trộn đi qua cùng bước sinh và kiểm chứng claim. Reranker chia cửa sổ token để giữ cả cuối đoạn dài; lấy điểm cao nhất giữa các cửa sổ có thể thiên về đoạn dài và cần theo dõi bằng ablation.
- Hồ sơ bản dịch, dữ liệu cách ly và phạm vi kho cần người duyệt. Không mặc định kho bao phủ toàn bộ Kinh Thánh.
- Báo cáo cũ không chứng nhận chất lượng phiên bản hiện tại. API miễn phí và model thay thế cần được kiểm chứng riêng.
- Chưa có phân quyền quản trị/tách kho theo người dùng. Chưa phù hợp công bố như dịch vụ nhiều người dùng có quyền tải lên/xóa chung.

Xem [evaluation/RELEASE_STATUS.md](evaluation/RELEASE_STATUS.md) để biết bằng chứng đã chạy và các điều kiện còn chờ chủ dự án/người duyệt. Không coi test tự động thành chứng nhận nội dung toàn bộ kho.

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

Mã nguồn dự án dùng [MIT License](LICENSE). Model và tài liệu bên thứ ba giữ giấy phép riêng. Không commit `.env`, bản sao `.env`, model hoặc backup kho. Khóa đã từng xuất hiện trong chat/Git phải được thu hồi tại tài khoản phát hành; `.gitignore` và bỏ theo dõi file không vô hiệu hóa khóa hoặc xóa nó khỏi lịch sử Git.
