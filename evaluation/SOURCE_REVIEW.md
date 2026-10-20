# Bằng chứng xuất xứ bản PDF đang dùng

Đối chiếu ngày 2026-10-19 trên bản gốc được lưu trong snapshot, SHA-256 `80350e916f8679d884f76c479cf3f4baa87cb3c41befec266efb44944043a66a`. PDF có 3.163 trang. Số trang bên dưới tính từ đầu file, bắt đầu bằng 1.

Trang 4 ghi bản văn theo bản dịch của Nhóm Phiên Dịch Các Giờ Kinh Phụng Vụ và ghi đơn vị chuẩn bị bản dùng trên Internet là Vietnamese Missionaries in Taiwan. Đã xem hình render trang này để xác nhận văn bản trích xuất nằm đúng trên trang. Trang 6 ghi ngày của lời giới thiệu là 05/05/1998; đây không xác định ngày phát hành hoặc phiên bản của file PDF đã sửa font.

Đề xuất trường `translation`: `Nhóm Phiên Dịch Các Giờ Kinh Phụng Vụ`. Đây là thông tin do bản PDF tự ghi; chưa đối chiếu với bản phát hành chính thức. Chưa có URL nơi chủ dự án nhận file, số ấn bản hoặc căn cứ quyền phân phối. Không suy người dịch từ trường `author` trong metadata PDF.

Giữ `complete=false`, `reviewed_by` trống và quy điển chưa xác nhận cho đến khi đối chiếu danh sách sách/chương/câu. Người duyệt cần xử lý 219 bản ghi cách ly, 209 vấn đề cấu trúc và xác minh bản PDF sửa font có bảo toàn bản văn. Cần kiểm tra các sách có hệ đánh số khác, nhãn câu gộp và câu trùng/xung đột.

Không sửa metadata kho đang hoạt động để thay cho một phiên nạp và nghiệm thu nguồn mới. Chỉ áp dụng hồ sơ đã được duyệt qua quy trình ingest có giao dịch. Báo cáo và hình đối chiếu cục bộ nằm trong `release_checks/source-frontmatter.json` và `release_checks/source-frontmatter-page4.png`; bản PDF và nội dung nguồn không được thêm vào Git.
