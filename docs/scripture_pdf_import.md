# Đọc PDF Kinh Thánh — processing_version 3

Bộ đọc nhận tên bookmark đầy đủ, cho phép khác dấu nối/khoảng trắng và ký tự Ð/Đ. Hỗ trợ các nhãn Sử Biên Niên, SÁCH THÁNH VỊNH và Vua Ét-ra gặp trong bản PDF kết hợp. Không dùng tìm tên sách theo chuỗi con trong nội dung.

Mỗi sách dừng ở bookmark cấp đầu kế tiếp, kể cả khi bookmark đó chưa được nhận ra. Phần dẫn nhập giữa các sách được lưu riêng; bookmark sách đánh số nhưng chưa hiểu sẽ chặn nạp thay vì gộp nội dung vào sách trước.

Tiêu đề chương lặp cùng chương không làm đứt câu qua trang. Các sách chỉ có một chương vẫn bị chặn nếu xuất hiện chương khác 1. Thánh Vịnh hỗ trợ cả số câu nhỏ ở đầu câu và dạng (1), (2) ở đầu dòng. Giữ riêng các bản ghi xung đột để kiểm tra, không bỏ kiểm tra cấu trúc.

## Kiểm tra Kinh-Thanh.pdf

- SHA256: 68e390de72c709beef39a452b337c061f81d229916cad618050d75365c5f9f90
- 3.163 trang; nhận đủ 73 sách, có 150 Thánh Vịnh.
- 35.095 bản ghi có thể nạp; 219 bản ghi được giữ riêng, còn cảnh báo đánh số cần đối chiếu.
- Không còn lỗi chặn cấu trúc. 87 kiểm thử hồi quy đã qua.
- Kết quả kiểm tra: D:/Code/scripture_rebuild/combined_fix_20260925_142201/parsed/report.json
- Đây là kiểm tra trích xuất và xác thực, chưa tạo embedding/nạp PDF vào collection đang dùng.

Khởi động lại ứng dụng, chọn Bản văn Kinh Thánh và tải lại PDF. Báo cáo trong chroma_db/scripture_exports sẽ được tạo lại khi nạp. Dữ liệu đang dùng không bị sửa bởi bước kiểm tra này.
