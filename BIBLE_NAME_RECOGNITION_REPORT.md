# Kiểm tra nhận diện cách viết tên

Ngày kiểm tra: 2026-10-10. Kho: `bible_workflow_v4`.

## Phạm vi và kết quả

Đã kiểm tra toàn bộ từ vựng học từ bản văn đang nạp: 2.923 nhóm, 2.978 cách viết có gạch nối. Từ vựng gồm tên người, địa danh và các từ viết hoa có gạch nối; đây không phải danh mục 2.978 nhân vật đã được xác minh.

25.960 biến thể đã được kiểm tra: hoa/thường, viết cách, nhiều dấu cách, khoảng trắng không ngắt dòng, gạch nối dài, viết liền, thiếu dấu và Unicode tổ hợp. Không có kết quả sai theo tiêu chí chuẩn hóa và bảo toàn tên mơ hồ. Tham chiếu `Ga 1:1–3` được giữ nguyên trong câu hỏi kiểm tra.

Có 52 nhóm trùng nhau khi bỏ dấu. 133 biến thể trong bộ kiểm tra được giữ nguyên vì chưa thể chọn một cách viết duy nhất. Đã bổ sung bước đưa mọi tên phù hợp trong các nhóm này vào từ vựng lập kế hoạch tìm kiếm; cả 52 nhóm đều giữ đủ ứng viên. Ví dụ `si mon` giữ cả `Si-môn` và `Si-mon`, cùng các tên đầy đủ thực sự xuất hiện trong nguồn. Việc này không xác nhận các tên là cùng một người.

Ví dụ đã kiểm tra trên kho thật:

| Đầu vào | Cách viết nguồn |
|---|---|
| Giu Se | Giu-se |
| Gio An, Gioan | Gio-an |
| Phao Lo | Phao-lô |
| Phê Rô | Phê-rô |
| Noe | Nô-ê |
| Đa Vít | Ða-vít |
| Sa Lo Mon | Sa-lô-môn |
| Eva | E-và |

Kiểm thử chức năng bổ sung còn bao phủ I-sa-ác, Gia-cóp, Ê-li-a, Giô-suê, Sa-mu-en, A-ha-ron, Đa-ni-en, Sa-ra, Rê-bê-ca, Ra-khen, Giu-đa, Mác-cô, Lu-ca, Ti-mô-thê, Ê-li-sa-bét và Da-ca-ri-a. Luồng lập kế hoạch được kiểm tra với nhiều tên và bản sao lịch sử, không sửa nội dung hội thoại đang hiển thị.

Chi tiết máy đọc được: `bible_workflow_data/evaluation/corpus-name-audit.json`.

Toàn bộ 144 kiểm thử chức năng đạt sau thay đổi. Kiểm thử từ vựng xác nhận giữ mọi cách viết có thể phù hợp, không trộn phần mở rộng tên và không đưa cách viết có dấu xung đột vào nhóm ứng viên.

## Giới hạn

Đây là kiểm tra nhận diện cách viết và cung cấp ứng viên tìm kiếm, không phải 25.960 lượt hỏi đáp bằng model. Chưa kiểm tra trực tiếp giao diện hoặc hỏi đáp từng nhân vật.

Cách phiên âm thay đổi chữ cái như `David`/`Ða-vít` hoặc `Abraham`/`Áp-ra-ham` cần từ vựng đối chiếu đã duyệt; không tự đồng nhất bằng độ giống chữ. Tên chưa xuất hiện trong nguồn với dạng có gạch nối không được tự học bởi cơ chế hiện tại. Nhiều người cùng tên vẫn phải phân biệt bằng nội dung và nguồn.

Không cần nạp lại PDF. Khởi động lại Chainlit để tiến trình đang chạy nạp mã mới.
