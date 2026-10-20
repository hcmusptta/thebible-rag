# Trạng thái nghiệm thu — 2026-10-19

Chưa đủ điều kiện tuyên bố hoàn tất hoặc phát hành công khai. Các sửa lỗi và công cụ bên dưới đã có trong mã nguồn; việc duyệt nội dung, thu hồi khóa và nghiệm thu cấu hình API còn cần bằng chứng riêng.

| Mục kiểm toán | Đã thực hiện | Điều kiện còn thiếu |
|---|---|---|
| 1. Nguồn generic hạ mức kiểm tra | Mọi nguồn/nguồn trộn dùng luồng có claim và bằng chứng; test tái hiện nhánh bypass. | Duyệt chất lượng trên nhiều loại tài liệu thật. |
| 2. Danh sách thiếu ý sau lọc | Kiểm tra lại số mục và tính duy nhất sau judge; chỉ sửa một lần, sau đó từ chối nếu thiếu. | Nghiệm thu nội dung theo tiêu chí đủ ý. |
| 3. Đầu ra bị cắt | Ollama/Google kiểm tra lý do kết thúc, HF kiểm tra token/EOS; JSON/schema được xác thực. Lỗi provider tách khỏi thiếu bằng chứng. Reasoning mặc định tắt, có cấu hình. | Kiểm tra model thay thế bằng dữ liệu thật. |
| 4. Phạm vi kho nguồn | Xuất audit chỉ đọc, băm PDF gốc, so snapshot và vector, đưa vấn đề vào hàng đợi duyệt. | Xác định bản dịch, quy điển, nguồn phát hành và duyệt các vấn đề. |
| 5. Chấm từ khóa sai nghĩa | Giữ `regression_passed` làm hồi quy; đáp án sinh chưa duyệt giữ `passed=null`. Review gắn với hash câu hỏi/đáp án/nguồn. | Người đọc chấm chủ thể, quan hệ, phủ định, đủ ý và hỗ trợ từ nguồn. |
| 6. Benchmark sai cấu hình/nguồn cũ | Dùng provider đang cấu hình; lưu model trả lời/judge, retrieval, revision, danh mục nguồn, mã băm code/testset. Dừng khi danh tính nguồn không khớp. Thêm mẫu tự soạn để onboarding. | Testset thật đủ các loại nguồn và tình huống; các mẫu thiếu provenance vẫn chưa được chứng nhận. |
| 7. Hiệu quả từng bước | Có script so BM25, vector, hybrid, rerank và đầy đủ; có biến thể đáp án chưa kiểm chứng/đã kiểm chứng để đọc offline. | Pilot không thay thế ablation có người chấm trên tập đủ lớn. |
| 8. Hiệu chuẩn judge | Có biểu mẫu, tiêu chí, thống kê judge chấp nhận sai so với người đọc và hướng dẫn holdout độc lập. | Chưa có người đọc ký duyệt hoặc holdout độc lập. Không tự dùng model ký thay. |
| 9. Reranker cắt 512 token | Chia cửa sổ có chồng lấn theo tokenizer, kiểm tra ngân sách/điểm; giữ bằng chứng cuối đoạn, ghi số cửa sổ và số đoạn vượt giới hạn. | Đo tác động tổng thể; điểm max giữa cửa sổ có thể thiên về đoạn dài. |
| 10. Phát hành | Bỏ theo dõi `.env copy` và backup, giữ bản trên đĩa; mở rộng ignore; tên module chữ thường; thêm MIT LICENSE, dữ liệu mẫu, hướng dẫn cài. | Chủ tài khoản xác nhận **chưa thu hồi khóa**. Lịch sử Git còn khóa tại commit `f666d98b4f74`. Cần thu hồi và chọn cách xử lý lịch sử trước công khai. |
| 11. Local và cấu hình nhẹ | Có probe provider thật, đo RAM/VRAM toàn máy, thời gian và request/token. Test giả lập key sai, quota/429, timeout, model lỗi và đầu ra bị cắt. | Chưa nghiệm thu API thật bằng khóa mới hoặc máy cấu hình nhẹ; chưa chứng nhận hạn mức miễn phí đủ tải. |

## Bằng chứng dữ liệu hiện tại

Audit `bible_workflow_data/evaluation/corpus-review.json` xác nhận 35.164 vector khớp số câu canonical; mã băm bản gốc khớp edition đang lưu. Còn 219 bản ghi cách ly và 209 vấn đề: 135 khoảng thiếu câu, 20 thứ tự câu, 1 chuỗi chương, 2 nhãn rỗng, 51 câu xung đột. Đây là vấn đề cần đối chiếu, không phải kết luận mọi mục đều là lỗi parser.

Hồ sơ nguồn đang chạy vẫn `complete=false`, bản dịch trống, quy điển chưa xác định. Đã tìm thấy thông tin về bản dịch ở trang PDF 4 và lưu hồ sơ đề xuất trong [SOURCE_REVIEW.md](SOURCE_REVIEW.md); cần đối chiếu xuất xứ/phiên bản và duyệt trước khi nạp lại. Không thay cờ hoàn tất hoặc đưa dữ liệu cách ly trở lại retrieval khi chưa duyệt. Tuyên bố hỗ trợ hiện chỉ áp dụng phần nguồn đã nạp và được phép tra cứu.

Testset Kinh Thánh 25 câu ở thư mục cha, được chọn qua `GOLDEN_TESTSET_PATH`, đã được đối chiếu lại với `Kinh_Thanh_Da_Sua_Font.pdf` ngày 2026-10-19, revision `2026-10-19-font-pdf-v1`. SHA-256 của PDF, document và edition đang nạp đều là `80350e916f8679d884f76c479cf3f4baa87cb3c41befec266efb44944043a66a`. Cả 74 đoạn trích khớp văn bản trên trang PDF được ghi lại; số trang tính từ 1, gồm cả bìa và mục lục. Kiểm tra danh tính nguồn của ứng dụng đạt 25/25, không có mẫu thiếu provenance. Bản trước khi sửa và báo cáo đối chiếu nằm tại `release_checks/golden_pdf/golden-before.json` và `validation.json`. Đây là đối chiếu nguồn, chưa phải kết quả chạy benchmark hay ký duyệt nội dung của người đọc.

Giữ đủ 25 câu: `kt_010` có đáp án tại 1 Vua 3:5–9, trang PDF 692, nhưng các câu nguồn tương ứng hiện bị cách ly vì xung đột nhận diện chương. Metadata ghi rõ tình trạng tại thời điểm đối chiếu; không đổi đáp án, loại mẫu hoặc đưa dữ liệu cách ly vào retrieval để nâng điểm. 24 mẫu còn lại có đủ các câu tham chiếu trong phần corpus được phép tra cứu; điều này không bảo đảm retrieval sẽ tìm được chúng. Testset tự soạn trong thư mục ứng dụng vẫn dành cho onboarding công khai.

## Đo model local

Probe `release_checks/ollama-local.json` dùng Windows, Python 3.11.9, RAM khoảng 32 GB và RTX 5060 Laptop 8 GB; model `qwen3.5:9b`, Q4_K_M, reasoning tắt. Một yêu cầu JSON/schema thành công: 19,34 giây, 1 request, 20 token vào và 10 token ra.

RAM toàn máy đạt khoảng 29,37 GiB, VRAM 7,64 GiB trong phép thử. Các số này bao gồm chương trình khác; không phải dung lượng riêng của model, không phải cấu hình tối thiểu, và không phải benchmark một lượt hỏi đáp RAG đầy đủ. Xem `measurement_scope` trong báo cáo gốc. Chưa có kết quả thành công với API thật hoặc máy yếu.

## Pilot so sánh retrieval

Cùng ba câu phát triển về Maria, Mô-sê và A-đam trên kho đang dùng; chưa có người chấm đáp án. Bảng đầu dùng encoder `auto`, gồm cả lượt tải model ban đầu. Các chương trình khác trên máy có thể ảnh hưởng độ trễ. Đây là bằng chứng chẩn đoán trên ba câu, chưa phải SLA hoặc kết luận thống kê về chất lượng toàn kho.

| Profile | Hit theo tham chiếu | Lỗi | Trung vị lượt thành công |
|---|---:|---:|---:|
| BM25 | 2/3 | 0 | 1,61 giây |
| Vector | 3/3 | 0 | 2,63 giây |
| Hybrid | 3/3 | 0 | 2,92 giây |
| Hybrid + rerank | 2/3 | 0 | 7,86 giây |
| Đầy đủ, encoder auto | 1/1 lượt hoàn thành | 2 timeout | 210,51 giây |
| Đầy đủ, embedding CPU | 3/3 | 0 | 26,61 giây |

Báo cáo gốc: `bible_workflow_data/evaluation/ablation-current.json` và `ablation-cpu-embedding.json`. Lượt đầy đủ với embedding CPU chạy lại riêng sau khi dỡ model Ollama khỏi bộ nhớ, trên cùng ba câu; lần đầu 63,11 giây, hai lần sau 26,61 và 26,25 giây. Không xem hai lần chạy khác trạng thái bộ nhớ này là thí nghiệm cô lập hoàn toàn tác động của device.

Luồng đầy đủ với embedding CPU dùng tổng 24 request, 52.407 token vào và 472 token ra: **8 request mỗi câu chỉ để truy xuất**, chưa sinh và kiểm chứng đáp án. Chi phí này cần tính khi chọn API miễn phí. Những lượt timeout trong báo cáo encoder auto chưa ghi đủ request/token; không coi số token của lượt thành công là tổng chi phí của cả lần chạy. Script hiện ghi cả thời gian và request khi lỗi.

Chưa có bằng chứng reranker hoặc chọn nguồn bằng LLM cải thiện hit-rate trên pilot này; chúng tăng độ trễ. Chưa đủ mẫu và chưa chấm đáp án để quyết định bỏ bước trên toàn bộ loại tài liệu. Giữ kết quả là chẩn đoán, tiếp tục so với holdout có người đọc. Reranker đã gặp đầu vào dài hơn 512 token trong lượt Mô-sê và xử lý bằng cửa sổ; không cắt bỏ phần cuối.

## Cài đặt và luồng mẫu đã kiểm tra

Đã cài dependency vào môi trường Python 3.11.9 mới trên Windows và chạy bản sao mã nguồn không có `.env`, model hoặc kho dữ liệu riêng. `PYTHON_DOTENV_DISABLED=1` ngăn tìm nhầm `.env` ở thư mục cha; kho unit test được tách riêng. **220 test qua**, `pip check` không có xung đột. Kiểm tra 238 file Python của RAGAS trong môi trường mới khớp bản ghi hash của package, không có bản vá trực tiếp trong `site-packages`.

Requirements đã bổ sung `rapidfuzz` cho metric không dùng LLM, dùng `langchain-community==0.4.1` để tương thích import của RAGAS 0.4.3, và khóa bộ adapter Instructor/OpenAI. Những phép kiểm tra này không chứng nhận Python/hệ điều hành khác hoặc toàn bộ phụ thuộc bắc cầu trong tương lai. Các phiên bản đã giải được lưu cục bộ tại `release_checks/clean-freeze.txt`; log test ở `release_checks/clean-tests.log`.

Phép thử với model thật dùng tài liệu Atlas tự soạn, embedding CPU, reranker tắt và Ollama `qwen3.5:9b`. Kho kiểm tra riêng đã nạp dữ liệu mẫu; phép đo cuối không bao gồm tải package/model từ Internet. Năm ca qua kiểm tra hồi quy, cả năm vẫn chờ người đọc duyệt (`passed=null`). Trạng thái thiếu thông tin được xuất riêng trong JSON, không cần tạo claim về giá trị hoặc danh tính mà nguồn không cung cấp.

| Ca mẫu | Thời gian hỏi đáp | Lần gọi LLM | Token vào / ra |
|---|---:|---:|---:|
| Danh sách hai ý | 15,06 giây | 3 | 3.036 / 107 |
| Thiếu ngân sách | 3,78 giây | 2 | 1.837 / 45 |
| Quan hệ trước/sau | 5,91 giây | 3 | 2.803 / 85 |
| Câu hỏi nối tiếp | 5,75 giây | 3 | 2.758 / 88 |
| Thiếu người sáng lập | 2,97 giây | 2 | 1.831 / 39 |

RAGAS nhanh trên năm câu onboarding có `non_llm_context_recall=1.0`; các câu dùng cùng một tài liệu nhỏ nên điểm này chỉ xác nhận luồng cài đặt/truy xuất. Không dùng nó làm điểm nghiệm thu nội dung Kinh Thánh hoặc toàn hệ thống. Báo cáo có usage từng mẫu và manifest tại `release_checks/sample-ragas.json`; báo cáo hỏi đáp tại `release_checks/sample-e2e.json`. Biểu mẫu `release_checks/sample-review-form.json` để trống người duyệt và tiêu chí.

Trong lượt thử mẫu, RAM toàn máy đạt khoảng 24,95 GiB và VRAM 7,60 GiB. Phạm vi đo gồm các chương trình khác và cả lượt RAGAS; không suy ra nhu cầu bộ nhớ riêng của hệ thống hoặc cấu hình tối thiểu từ các số này.

## Tái kiểm tra

Các lệnh nằm trong README và [REVIEW_GUIDE.md](REVIEW_GUIDE.md). Kết quả benchmark, dữ liệu nguồn và môi trường cài thử nằm trong các thư mục được Git bỏ qua. Khi đổi code, model, kho hoặc testset, tạo báo cáo mới; không dùng báo cáo cũ để chứng nhận bản mới.

`scripts/check_release.py` giữ cổng file/lịch sử ở trạng thái chưa đạt khi còn khóa trong lịch sử, kể cả khi danh sách file hiện tại sạch. Không sửa báo cáo thành đạt bằng tay; việc xử lý khóa cần thực hiện tại tài khoản phát hành.
