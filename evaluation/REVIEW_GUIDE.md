# Nghiệm thu và hiệu chuẩn

`golden_testset.json` và `sample_behavior_cases.json` dùng tài liệu tự soạn trong `samples/` để kiểm tra cài đặt. Chúng không chứng minh chất lượng trên Kinh Thánh, giáo lý hoặc chú giải và không phải holdout độc lập.

## Dữ liệu nguồn

Chạy `python scripts/audit_corpus.py --output bible_workflow_data/evaluation/corpus-review.json`. File có mã SHA-256 của bản gốc, kiểm tra số bản ghi và hàng đợi vấn đề cần duyệt. Lệnh chỉ đọc kho, không thay metadata hoặc tự đưa dữ liệu cách ly trở lại truy xuất.

Người duyệt phải đối chiếu trang PDF gốc, ghi bản dịch, quy điển, nguồn phát hành và quyết định cho từng vấn đề. Cảnh báo hợp lệ được giải thích; lỗi parser phải sửa ở parser rồi nạp lại theo giao dịch an toàn. Không tự đặt `complete=true` để vượt kiểm tra.

## Chấm câu trả lời

Chạy bộ câu hỏi bằng `evaluate_bible_answers.py --answers --cases <cases.json> --output <results.json>`. Các kiểm tra từ khóa/tham chiếu nằm trong `regression_passed`; câu trả lời sinh bởi model để `passed=null` cho đến khi có người duyệt. Mã thoát 2 nghĩa là còn chờ duyệt, mã 1 là có lỗi kiểm tra, mã 0 là mọi mẫu được chấp nhận hoặc là kiểm tra truy xuất tự động.

```powershell
python scripts/review_evaluation.py results.json --output review-form.json
python scripts/review_evaluation.py results.json --reviews review-form.json --output reviewed-results.json
```

Giữa hai lệnh, người đọc điền tên và bốn tiêu chí boolean: đúng nội dung, được nguồn hỗ trợ, đủ ý và giữ phạm vi. Ghi rõ lỗi phủ định, chủ thể, người nói, nhãn câu hoặc trộn bản dịch trong `notes`. Không lấy từ khóa trùng làm bằng chứng đúng nghĩa. Review bị từ chối nếu câu hỏi, đáp án hoặc nguồn đã đổi.

Báo cáo `judge_false_accepts` đếm những trường hợp được bộ kiểm chứng claim tự động chấp nhận nhưng người đọc bác bỏ ở ít nhất một tiêu chí; bảng `judge_false_accepts_by_criterion` chỉ rõ từng loại bất đồng. Đây là thống kê trên các mẫu đã duyệt, không phải bảo đảm cho toàn bộ kho. Nó không tự hiệu chuẩn các điểm số RAGAS: khi dùng RAGAS để nghiệm thu, người đọc phải so riêng từng metric với đáp án/bằng chứng, nêu ngưỡng chấp nhận và ghi các trường hợp bất đồng. Chưa có kết quả chấm tay thì chưa được tuyên bố judge đã được hiệu chuẩn.

## Tập nghiệm thu độc lập

Người đọc khác chuẩn bị câu hỏi chưa dùng để sửa prompt, tách khỏi `bible_basic_questions.json` và các bộ mẫu phát triển. Bao phủ Kinh Thánh, giáo lý, chú giải, nguồn trộn, không có đáp án, trùng tên, phủ định, lịch sử hội thoại và nguồn thiếu/cách ly. Ghi mã phiên bản, người soạn và bản nguồn. Không tự coi câu hỏi do model vừa tạo là một tập đã được chuyên gia duyệt.

Giữ kín tập nghiệm thu khi tối ưu. Nếu dùng một mẫu để sửa lỗi, chuyển mẫu đó sang hồi quy và bổ sung mẫu nghiệm thu mới. Cần người đọc thật để hoàn tất bước này; công cụ không tự ký thay.

## So sánh từng bước

```powershell
python scripts/benchmark_ablation.py --cases bible_basic_questions.json --output bible_workflow_data/evaluation/ablation.json
```

Các profile dùng cùng câu hỏi, bộ lọc và kho: BM25, vector, hybrid, hybrid + CrossEncoder, luồng đầy đủ. Những đường tra tham chiếu trực tiếp vẫn được ghi rõ trong diagnostics; không diễn giải chúng như phép so sánh semantic retrieval. `--answers` ghi thêm bản nháp chưa kiểm chứng và đáp án kiểm chứng để người đọc chấm; bản nháp chỉ nằm trong báo cáo offline, không được ứng dụng trả cho người dùng.

Đánh giá theo chất lượng, độ trễ, request/token và tài nguyên. Không tắt hàng rào kiểm chứng chỉ để đạt tốc độ hoặc tăng điểm trên tập phát triển. Chỉ bỏ một bước khi số liệu trên tập phù hợp cho thấy việc đó không làm mất yêu cầu chất lượng.

## Phát hành

Chạy `python scripts/check_release.py`. Script không in giá trị khóa và không xóa dữ liệu. Mã thoát 2 giữ cổng phát hành đóng khi còn dấu vết khóa trong lịch sử; mã 1 là lỗi trong danh sách file, mã 0 là qua kiểm tra file/lịch sử mà script hỗ trợ. Khóa từng nằm trong lịch sử Git vẫn cần chủ tài khoản thu hồi; thêm `.gitignore` không vô hiệu hóa khóa. Sau khi thu hồi, chủ repo cần chọn tạo repo mới từ mã nguồn sạch hoặc xử lý lịch sử trước khi công khai. Script không tự viết lại lịch sử của bạn. LICENSE MIT áp dụng cho mã nguồn của dự án, không cấp lại quyền đối với model hoặc tài liệu của bên khác.
