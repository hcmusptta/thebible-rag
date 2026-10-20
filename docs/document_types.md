# Phân loại tài liệu

Kho vector dùng chung model embedding; phân biệt nguồn bằng metadata và bộ lọc truy xuất.

| Loại | document_type | Metadata |
|---|---|---|
| Bản văn Kinh Thánh | scripture | translation, edition_id, book_code, book, chapter, verse_start, verse_end |
| Chú giải / hướng dẫn giải thích | commentary | author, title, scripture_references, heading |
| Giáo lý | catechism | title, section_number, topic, heading |

Tất cả giữ source, document_id, page và page_end. Nhãn bible cũ được nhận như scripture khi lọc và hiển thị; không sửa hàng loạt database cũ hoặc tạo lại embedding. Khi nạp lại, tài liệu mới dùng nhãn scripture. PDF thông thường vẫn được giữ ở nhóm generic (Tài liệu khác).

## Sử dụng

1. Cài đặt chỉ có **Loại tài liệu sẽ nạp** và **Nguồn dùng để trả lời**.
2. Chọn loại trước khi tải file: Bản văn Kinh Thánh, Chú giải, Giáo lý hoặc Tài liệu khác. Mặc định là Bản văn Kinh Thánh. Lựa chọn áp dụng cho cả nút Thêm PDF lẫn kéo thả file; không tự suy đoán loại từ tên file hoặc trang bìa trong luồng giao diện.
3. Nguồn dùng để trả lời mặc định là Tất cả; chọn một loại để giới hạn truy xuất. Kho tài liệu hiển thị theo nhóm. Với Tất cả, câu hỏi chỉ rõ sách–chương–câu vẫn ưu tiên tra bản văn.
4. Không còn trường nhập tên tài liệu, tác giả, bản dịch hoặc chủ đề trong Cài đặt. Tên tài liệu lấy từ metadata PDF hoặc tên file; tác giả/bản dịch chưa biết được ghi Chưa xác định. Chủ đề có thể lấy từ đề mục. Trường Author kỹ thuật của PDF lưu riêng ở pdf_author.

Chỉ scripture đi qua bộ kiểm tra sách–chương–câu. Chú giải và giáo lý tách theo đề mục/đoạn, giữ nội dung qua trang. Đề mục dựa trên bookmark hoặc kiểu chữ đậm; số mục giáo lý dựa trên số mở đầu đoạn. PDF scan vẫn cần OCR. API/CLI nội bộ vẫn hỗ trợ tự nhận diện và metadata tùy chọn cho các tác vụ xử lý dữ liệu; giao diện luôn truyền loại đã chọn.

scripture_references giữ tham chiếu được nhận ra trong đoạn chú giải, không khẳng định mọi tham chiếu đó là chủ đề chính. Không tự tạo tham chiếu khi đoạn không ghi rõ. topic dùng metadata được truyền qua API, hoặc đề mục nếu có. Các trường trống phản ánh thông tin chưa xác định.

Nạp lại cùng PDF nhưng đổi loại hoặc metadata sẽ thay thế phiên bản cũ theo giao dịch nạp hiện có; lỗi/cancel trước commit giữ dữ liệu cũ. Không trộn chunk thuộc hai cách phân loại của cùng phiên bản file.

## PDF hướng dẫn của Ủy ban Kinh Thánh

UBKTGH_HUONG DAN_GIAI_THICH_KINH_THANH.pdf (kể cả tên có dấu cách) thuộc commentary, không cần bookmark từng sách. Chọn Chú giải trong Cài đặt trước khi tải lên. Kiểm tra bản PDF thực tế 177 trang đã đọc đủ trang, tạo được các đoạn có đề mục; lần kiểm tra không ghi vào kho đang dùng.
