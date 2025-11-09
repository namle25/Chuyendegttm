# Ứng dụng nhận diện chỗ trống bãi giữ xe

Tệp này mô tả chức năng, luồng dữ liệu, đầu vào/đầu ra, các trường hợp biên và hướng triển khai nhanh cho ứng dụng nhận diện chỗ trống bãi đỗ xe.

## Mục tiêu

- Tự động phát hiện và theo dõi các vị trí đỗ xe trống (và đã có xe) trong một bãi giữ xe dựa trên hình ảnh/stream video.
- Cung cấp API/luồng dữ liệu để trả về trạng thái chỗ trống theo thời gian thực cho giao diện người dùng hoặc hệ thống khác.

## Các chức năng chính

1. Nhận dạng chỗ đỗ (Slot Detection)

   - Phát hiện cấu trúc chỗ đỗ trên ảnh tĩnh hoặc video (tọa độ bounding box hoặc polygon cho từng slot).
   - Hỗ trợ cấu hình trước: dùng file cấu hình (ví dụ `polygons.json`) để lưu vị trí cố định của từng chỗ đỗ.

2. Phát hiện trạng thái (Occupancy Classification)

   - Với mỗi chỗ đỗ đã xác định, phân loại là "trống" hoặc "có xe".
   - Sử dụng mô hình học sâu (ví dụ `best.pt`) hoặc thuật toán truyền thống (background subtraction, thresholding) tùy cấu hình.

3. Theo dõi theo thời gian (Temporal Smoothing / Tracking)

   - Loại bỏ nhầm lẫn do nhiễu bằng cách áp dụng lọc temporal (ví dụ majority voting trên N khung, debounce time).
   - Hỗ trợ phát hiện thay đổi trạng thái (ví dụ: chỗ từ trống -> có xe) và gửi thông báo/event.

4. Giao tiếp và API

   - Cung cấp REST API hoặc WebSocket để truy vấn trạng thái chỗ trống theo thời gian thực.
   - Xuất logs / trạng thái định kỳ cho `runtime_status.py` hoặc service giám sát.

5. Gửi thông báo/Email (tuỳ chọn)

   - Khi chỗ trống thay đổi, có thể gửi email hoặc thông báo theo cấu hình `email_config.py` / `email_service.py`.

6. Giao diện người dùng (Không bắt buộc trong backend)
   - Bảng điều khiển hiển thị sơ đồ bãi và màu sắc biểu thị trạng thái chỗ đỗ.
   - Lọc, thống kê (tổng số chỗ trống, chỗ trống theo khu vực), và lịch sử thay đổi.

## Luồng dữ liệu (Data Flow)

1. Nguồn dữ liệu: camera IP hoặc video file.
2. Tiền xử lý: resize ảnh, chỉnh màu nếu cần, crop theo vùng quan tâm.
3. Xác định vị trí chỗ đỗ: tải `polygons.json` chứa danh sách vị trí (polygon hoặc box) hoặc chạy module phát hiện slot tự động.
4. Cho mỗi slot: trích xuất vùng ảnh tương ứng và chạy mô hình phân loại (`best.pt`) hoặc thuật toán.
5. Áp dụng lọc thời gian để ổn định kết quả.
6. Cập nhật trạng thái runtime, lưu log, và trả về qua API / gửi thông báo.

## Đầu vào / Đầu ra

- Đầu vào

  - Luồng video (camera RTSP/HTTP) hoặc ảnh tĩnh.
  - File cấu hình vị trí chỗ đỗ: `polygons.json` (mã định danh slot, tọa độ polygon/box, meta).
  - Mô hình phân loại (ví dụ `best.pt`).
  - Cấu hình email trong `email_config.py` nếu cần cảnh báo.

- Đầu ra
  - JSON trạng thái: danh sách slot với id, trạng thái (empty/occupied), confidence, timestamp.
  - Endpoint REST: `/api/slots` trả trạng thái hiện tại.
  - Event/Notification khi trạng thái thay đổi.
  - Log runtime (có thể qua `runtime_status.py`).

## Định dạng JSON mẫu cho một slot

```json
{
  "id": "slot-01",
  "polygon": [[x1,y1],[x2,y2],[x3,y3],[x4,y4]],
  "status": "empty",
  "confidence": 0.92,
  "last_changed": "2025-11-09T12:34:56Z"
}
```

## Các ràng buộc và giả định

- Camera cố định, góc nhìn không thay đổi nhiều so với cấu hình ban đầu.
- `polygons.json` cung cấp định nghĩa vị trí chỗ đỗ chuẩn xác; nếu không có, cần module tự động phát hiện.
- Mô hình `best.pt` đã được huấn luyện cho môi trường tương tự (góc máy, ánh sáng).
- Hệ thống phải xử lý được nhiễu do thay đổi ánh sáng, bóng, và xe tạm dừng.

## Các trường hợp biên (Edge cases)

- Ánh sáng yếu hoặc ánh sáng ngược (backlight) làm giảm độ chính xác.
- Xe đỗ sai vị trí che một phần nhiều slot lân cận.
- Camera bị rung hoặc thay đổi góc -> cần phát hiện camera shift và báo lại.
- Tốc độ khung hình thấp hoặc mất khung -> tăng debounce hoặc dùng frame interpolation.

## Kiến trúc triển khai nhanh (Mini contract)

- Inputs: RTSP URL hoặc thư mục ảnh + `polygons.json` + `best.pt`.
- Output: REST API trả JSON trạng thái slot; log thay đổi; email tùy chọn.
- Error modes: mất luồng video (retry), mô hình lỗi (fallback rule-based), cấu hình thiếu (error + exit).

## Cách chạy nhanh (gợi ý)

1. Cấu hình `polygons.json` với danh sách slot.
2. Đặt file mô hình `best.pt` vào thư mục gốc (hoặc chỉ đường dẫn).
3. Cấu hình email nếu cần trong `email_config.py`.
4. Chạy `main.py` để khởi động dịch vụ xử lý video và API.

## Các bước nâng cao / gợi ý cải tiến

- Thêm web dashboard với sơ đồ bãi.
- Hỗ trợ nhiều camera và map slot trên nhiều vùng.
- Thêm chế độ huấn luyện online: ghi ảnh mẫu cho từng slot để tinh chỉnh mô hình.
- Tích hợp Redis/DB để lưu lịch sử trạng thái và làm analytics.

## DEMO

![alt text](<Ảnh chụp màn hình 2025-11-09 210906.png>)
![alt text](<Ảnh chụp màn hình 2025-11-09 210937.png>)
![alt text](<Ảnh chụp màn hình 2025-11-09 210953.png>)
![alt text](<Ảnh chụp màn hình 2025-11-09 211003.png>)
