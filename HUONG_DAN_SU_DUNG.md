# 📖 HƯỚNG DẪN SỬ DỤNG HỆ THỐNG FBEM (Headless Facebook Bridge & MCP)

Chào mừng bạn đến với **FBEM** — Hệ thống tự động hóa đăng bài (Reels, Ảnh/Album), quản lý đa tài khoản Facebook (Via) & Fanpage theo cơ chế **Capture & Replay** mô phỏng 100% thao tác người dùng thật (Native Web API), không bị bóp tương tác như Graph API thông thường.

Hệ thống hoạt động dưới dạng **Headless Daemon** được điều khiển hoàn toàn bởi **AI Agent thông qua giao thức MCP (Model Context Protocol)**.

---

## 📑 MỤC LỤC
1. [Yêu cầu hệ thống](#1-yêu-cầu-hệ-thống)
2. [Cài đặt & Khởi động nhanh](#2-cài-đặt--khởi-động-nhanh)
3. [Cài đặt Chrome Extension](#3-cài-đặt-chrome-extension)
4. [Nguyên lý hoạt động (Capture & Replay)](#4-nguyên-lý-hoạt-động-capture--replay)
5. [Tích hợp & Điều khiển bằng AI Agent qua MCP](#5-tích-hợp--điều-khiển-bằng-ai-agent-qua-mcp)
6. [Các MCP Tools có sẵn](#6-các-mcp-tools-có-sẵn)
7. [Các câu hỏi thường gặp & Xử lý sự cố (Troubleshooting)](#7-các-câu-hỏi-thường-gặp--xử-lý-sự-cố)

---

## 1. Yêu cầu hệ thống
- **Hệ điều hành**: Linux VPS, Windows hoặc macOS.
- **Python**: Phiên bản `>= 3.11` (Khuyên dùng `uv`).
- **Trình duyệt**: Google Chrome hoặc Brave / Microsoft Edge (để cài Chrome Extension và mở Facebook).

---

## 2. Cài đặt & Khởi động nhanh

### Bước 1: Tạo môi trường và cài đặt dependencies
```bash
uv venv
# Kích hoạt môi trường ảo:
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate

uv pip install -e .
```

### Bước 2: Khởi động Server Bridge (Headless Daemon)
```bash
python -m fbem.bridge
# Hoặc trên Windows:
start.bat
```
> Khi server chạy thành công:
> - **REST API**: `http://127.0.0.1:47102` (Swagger Docs tại `/docs`)
> - **Extension WebSocket**: `ws://127.0.0.1:9224`
> - **WebSocket Extension Port**: `ws://127.0.0.1:9224/ws`

👉 Truy cập vào trình duyệt: **[http://127.0.0.1:47102/ui/](http://127.0.0.1:47102/ui/)** để mở giao diện quản trị.

---

## 3. Cài đặt Chrome Extension

Extension đóng vai trò là "cầu nối" chạy trực tiếp trên phiên đăng nhập Facebook của bạn.

1. Mở trình duyệt Chrome (nơi đã đăng nhập tài khoản Facebook / Via của bạn).
2. Truy cập vào đường dẫn: `chrome://extensions/`
3. Bật công tắc **Developer mode (Chế độ dành cho nhà phát triển)** ở góc trên bên phải.
4. Bấm nút **Load unpacked (Tải tiện ích đã giải nén)**.
5. Chọn thư mục `extension` nằm trong thư mục dự án `fbem`.
6. Mở một tab Facebook (`https://www.facebook.com`) và đảm bảo tài khoản đã đăng nhập.
7. Mở Dashboard `http://127.0.0.1:47102/ui/` -> mục **Extensions**: bạn sẽ thấy tài khoản Facebook, tên Via, Avatar và danh sách Fanpage tự động xuất hiện!

> 💡 **Mẹo chạy nhiều tài khoản (Multi-Via):** Bạn có thể tạo nhiều **Chrome Profile** khác nhau, mỗi profile đăng nhập 1 nick Facebook và cài extension này. Hệ thống sẽ nhận diện từng profile với ID riêng biệt và hỗ trợ đăng song song!

---

## 4. Nguyên lý hoạt động (Capture & Replay)

FBEM sử dụng kỹ thuật **Passive Sniffing & Native Replay**:

1. **Lần đầu tiên (Capture - Bắt mẫu)**:
   - Bạn mở Facebook trên trình duyệt, tự tay đăng **1 video Reels** hoặc **1 bài ảnh** mẫu.
   - Extension sẽ tự động "bắt" cấu trúc gói tin upload (`rupload`, GraphQL mutations, token `fb_dtsg`) và lưu vào `template.json`.
2. **Các lần sau (Replay - Tự động hóa hoàn toàn)**:
   - Mỗi khi bạn bấm Đăng bài trên Dashboard hoặc ra lệnh qua AI Agent, FBEM sẽ tự động lấy video/ảnh mới + bóc tách token tươi của phiên duyệt hiện tại và gửi gói tin chuẩn khớp 100% với Facebook.
   - **Tác dụng**: Không bị thuật toán Facebook phạt giảm tương tác (như khi dùng Graph API của App), không lo checkpoint do hành vi lạ.
3. **Khi Facebook đổi giao diện / cập nhật payload**:
   - Chỉ cần tự tay đăng 1 bài trên trình duyệt để Extension ghi nhận template mới là hệ thống tự phục hồi ngay mà không cần sửa code!

---

## 5. Tích hợp & Điều khiển bằng AI Agent qua MCP

FBEM đi kèm một **MCP Server** (`Model Context Protocol`) chuẩn, cho phép các AI Agent như **Claude Desktop**, **Claude Code**, **Cursor**, **Antigravity**, **Cline** điều khiển 100% các tính năng của hệ thống.

### Cấu hình Agent (`claude_desktop_config.json` hoặc Cursor MCP settings):

```json
{
  "mcpServers": {
    "fbem": {
      "command": "uv",
      "args": ["run", "fbem-mcp"],
      "env": {
        "FBEM_BRIDGE_URL": "http://127.0.0.1:47102"
      }
    }
  }
}
```
*(Nếu server bridge chạy trên VPS từ xa, đặt `FBEM_BRIDGE_URL` thành domain VPS của bạn, ví dụ `https://fbem.yourdomain.com`).*

---

## 6. Các MCP Tools có sẵn cho AI Agent

| Nhóm | MCP Tool | Mô tả |
| :--- | :--- | :--- |
| **Kiểm tra trạng thái** | `health(extension_id?)` | Kiểm tra kết nối bridge, extension nào đang active, tab TTL. |
| | `capture_status(extension_id?)` | Xem mẫu upload Reels/Photos đã được ghi nhận chưa. |
| | `get_stats()` | Lấy thống kê số lượng accounts, số job đang đợi, đang chạy, thành công. |
| **Tài khoản & Fanpage** | `get_identity(extension_id?)` | Lấy danh tính (User / Page) của tab Facebook đang mở. |
| | `list_pages(extension_id?)` | Xem danh sách các Fanpage mà extension đã phát hiện. |
| | `list_accounts(extension_id?)` | Xem danh sách các account / page được cấu hình trong database. |
| | `switch_profile(target_id, extension_id?)` | Chuyển ngữ cảnh sang Page / Profile đích. |
| **Đăng bài** | `post_reel(video_path, caption, ...)` | Đăng 1 video Reels trực tiếp (hỗ trợ hẹn giờ `scheduled_publish_time`). |
| | `post_photos(image_paths, caption, ...)` | Đăng bài viết kèm 1 hoặc nhiều hình ảnh. |
| | `stage_media_from_url(url, filename?)` | Tải video/ảnh từ internet về thư mục media của server. |
| **Hàng đợi & Lịch sử** | `enqueue_job(kind, input_data, ...)` | Đẩy job đăng bài vào hàng đợi để dispatcher tự động xử lý. |
| | `list_jobs(status?, limit?)` | Xem danh sách job trong hàng đợi (`queued`, `running`, `completed`, `failed`). |
| | `cancel_job(job_id)` | Hủy 1 job đang chờ. |
| | `retry_job(job_id)` | Chạy lại 1 job bị lỗi. |
| | `get_history(limit?)` | Xem lịch sử các bài đã post, video ID và link bài viết. |

---

## 7. Các câu hỏi thường gặp & Xử lý sự cố

#### Q1: Tại sao Extension báo trạng thái Disconnected?
> **Xử lý**: Đảm bảo server `python -m fbem.bridge` đang chạy. Kiểm tra cổng WebSocket `9224` (hoặc cấu hình WSS qua Cloudflare nếu chạy remote).

#### Q2: Báo lỗi "No template captured for kind: reel"?
> **Xử lý**: Hệ thống chưa có mẫu cấu trúc đăng bài. Hãy mở tab Facebook trên trình duyệt máy bạn và tự tay đăng 1 video Reel thử nghiệm. Sau khi đăng xong, Extension sẽ tự ghi nhận mẫu và các lần sau Agent sẽ tự động 100%.

---

Chúc bạn có trải nghiệm tự động hóa tuyệt vời với FBEM! 🚀

